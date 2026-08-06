#!/usr/bin/env python3
"""Reference client for openmodels. Copy this into your fork and adapt it.

Deliberately boring and dependency-free (urllib only). Three properties matter more than
features, and they are the reason this file exists rather than a curl snippet in a README:

  1. Every downloaded blob is verified against its oid, and a mismatch aborts.
  2. Withdrawn models are denied by default. "merged" is not "approved", and `reverted` and
     `pr_only` models require an explicit opt-in.
  3. It falls back to the static mirror when the API is unreachable, so an outage degrades to
     a stale catalog rather than no models.

It will not tell you a model is safe. Nothing can, from this data. Qualify on your hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://openmodels.example"
MIRROR = "https://OWNER.github.io/openmodels"          # static fallback, always readable
SAFE_STATUSES = frozenset({"merged"})                   # default-deny reverted and pr_only
CHUNK = 1 << 20


class VerificationError(Exception):
  """A blob did not match its oid. Never proceed past this."""


def _get(url: str, timeout: int = 20) -> bytes:
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()


class CatalogUnavailable(Exception):
  """Neither the API nor the static mirror could be reached."""


def fetch_catalog(api: str = API, mirror: str = MIRROR) -> tuple[dict[str, Any], str]:
  """Live API first, static mirror second. Returns (catalog, source)."""
  failures = []
  # The mirror is a plain file on a CDN; it survives the API being down entirely.
  for url, source in ((f"{api}/v1/models?limit=1000", "api"), (f"{mirror}/index.json", "mirror")):
    try:
      return json.loads(_get(url)), source
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
      failures.append(f"{source}: {exc}")
  raise CatalogUnavailable("; ".join(failures))


def latest_status(bundle: dict[str, Any]) -> str:
  if "status" in bundle:
    return bundle["status"]
  return max(bundle["occurrences"], key=lambda o: o["date"])["status"]


def select(catalog: dict[str, Any], *, kind: str = "driving",
           allow_statuses: frozenset[str] = SAFE_STATUSES,
           name_contains: str | None = None) -> list[dict[str, Any]]:
  """Filter the catalog. Withdrawn models are excluded unless explicitly allowed."""
  bundles = catalog.get("bundles", [])
  out = []
  for bundle in bundles:
    if bundle.get("kind") != kind:
      continue
    if latest_status(bundle) not in allow_statuses:
      continue
    if name_contains and name_contains.lower() not in bundle.get("name", "").lower():
      continue
    out.append(bundle)
  return out


def download_verified(oid: str, dest: Path, api: str = API) -> Path:
  """Stream a blob to disk and verify it. The oid IS the sha256.

  Hashes while streaming so a 296 MB model never lands in memory, and unlinks on mismatch so a
  bad artifact cannot be picked up by a later run.
  """
  dest.parent.mkdir(parents=True, exist_ok=True)
  digest = hashlib.sha256()
  tmp = dest.with_suffix(dest.suffix + ".part")

  with urllib.request.urlopen(f"{api}/v1/files/{oid}/download", timeout=60) as response, \
       open(tmp, "wb") as handle:
    while chunk := response.read(CHUNK):
      digest.update(chunk)
      handle.write(chunk)

  actual = digest.hexdigest()
  if actual != oid:
    tmp.unlink(missing_ok=True)
    raise VerificationError(f"sha256 mismatch for {oid}: got {actual} — refusing blob")

  tmp.rename(dest)
  return dest


def provenance(bundle_id: str, api: str = API) -> dict[str, Any]:
  return json.loads(_get(f"{api}/v1/models/{bundle_id}/provenance"))


def pull(bundle_id: str, out_dir: Path, api: str = API) -> dict[str, Any]:
  """Download a whole bundle and report the constants that must be applied with it."""
  record = provenance(bundle_id, api)

  for entry in record["files"]:
    target = out_dir / entry["filename"]
    print(f"  {entry['role']:<12} {entry['filename']}  ({entry['size']/2**20:.0f} MB)")
    download_verified(entry["oid"], target, api)
    print(f"  {'':<12} verified sha256 == oid")

  if missing := record.get("host_constants_missing"):
    print(f"\n  WARNING: constants not found upstream: {', '.join(missing)}", file=sys.stderr)
    print("  They are absent, not zero. Do not substitute defaults.", file=sys.stderr)

  return record


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--api", default=API)
  parser.add_argument("--list", action="store_true", help="list installable models")
  parser.add_argument("--pull", metavar="BUNDLE_ID", help="download and verify a bundle")
  parser.add_argument("--out", default="models", type=Path)
  parser.add_argument("--include-withdrawn", action="store_true",
                      help="also show reverted and PR-only models (not recommended)")
  args = parser.parse_args()

  allowed = (frozenset({"merged", "reverted", "pr_only"}) if args.include_withdrawn
             else SAFE_STATUSES)

  if args.list:
    catalog, source = fetch_catalog(args.api)
    models = select(catalog, allow_statuses=allowed)
    print(f"{len(models)} models (source: {source}, statuses: {sorted(allowed)})\n")
    for bundle in models[-25:]:
      print(f"  {bundle['bundle_id']}  {bundle['introduced_by']['date'][:10]}  "
            f"{latest_status(bundle):<9} {bundle['name'][:44]}")
    return 0

  if args.pull:
    print(f"pulling {args.pull}")
    record = pull(args.pull, args.out, args.api)
    print("\nRequired companion configuration — apply these with the weights:")
    for key, value in sorted(record.get("host_constants", {}).items()):
      print(f"  {key} = {value}")
    print(f"\n{record['disclaimer']}")
    return 0

  parser.print_help()
  return 1


if __name__ == "__main__":
  sys.exit(main())
