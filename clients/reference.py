#!/usr/bin/env python3
"""Reference client for openmodels. Copy this into your fork and adapt it.

Deliberately boring and dependency-free (urllib only). Three properties matter more than
features, and they are the reason this file exists rather than a curl snippet in a README:

  1. Every downloaded blob is verified against its oid, and a mismatch aborts.
  2. Withdrawn models are shown, never hidden -- but always with their status, because
     "merged" is not "approved" and a user needs to see which is which.
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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = ""                                                # optional self-hosted API
MIRROR = "https://jjolano.github.io/openmodels"         # public static catalog
ALL_STATUSES = frozenset({"merged", "reverted", "pr_only"})   # listed, but always with status
MERGED_ONLY = frozenset({"merged"})
CHUNK = 1 << 20


class VerificationError(Exception):
  """A blob did not match its oid. Never proceed past this."""


def _get(url: str, timeout: int = 20) -> bytes:
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()


class CatalogUnavailable(Exception):
  """Neither the API nor the static mirror could be reached."""


def terminal_text(value: Any) -> str:
  """Render attacker-controlled provenance without terminal escape/control sequences."""
  return "".join(char if char.isprintable() else "�" for char in str(value))


def fetch_catalog(api: str = API, mirror: str = MIRROR) -> tuple[dict[str, Any], str]:
  """Live API first, static mirror second. Returns (catalog, source)."""
  failures = []
  # The mirror is a plain file on a CDN; it survives the API being down entirely.
  sources = [(f"{api.rstrip('/')}/v1/models?limit=1000", "api")] if api else []
  sources.append((f"{mirror.rstrip('/')}/index.json", "mirror"))
  for url, source in sources:
    try:
      return json.loads(_get(url)), source
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
      failures.append(f"{source}: {exc}")
  raise CatalogUnavailable("; ".join(failures))


def statuses(bundle: dict[str, Any]) -> frozenset[str]:
  if "statuses" in bundle:
    return frozenset(bundle["statuses"])
  if "status" in bundle:  # schema-1/API compatibility
    return frozenset({bundle["status"]})
  return frozenset(o["status"] for o in bundle["occurrences"])


def select(catalog: dict[str, Any], *, kind: str = "driving",
           allow_statuses: frozenset[str] = ALL_STATUSES,
           name_contains: str | None = None) -> list[dict[str, Any]]:
  """Filter the catalog. Every status is included by default; callers render it."""
  bundles = catalog.get("bundles", [])
  out = []
  for bundle in bundles:
    if bundle.get("kind") != kind:
      continue
    if not statuses(bundle) & allow_statuses:
      continue
    if name_contains and name_contains.lower() not in bundle.get("name", "").lower():
      continue
    out.append(bundle)
  return out


def download_verified(oid: str, dest: Path, url: str) -> Path:
  """Stream a blob to disk and verify it. The oid IS the sha256.

  Hashes while streaming so a 296 MB model never lands in memory, and unlinks on mismatch so a
  bad artifact cannot be picked up by a later run.
  """
  dest.parent.mkdir(parents=True, exist_ok=True)
  digest = hashlib.sha256()
  tmp = dest.with_suffix(dest.suffix + ".part")

  with urllib.request.urlopen(url, timeout=60) as response, \
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


def provenance(bundle_id: str, catalog: dict[str, Any], api: str = API) -> dict[str, Any]:
  if api:
    try:
      return json.loads(_get(f"{api.rstrip('/')}/v1/models/{bundle_id}/provenance"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
      pass
  for bundle in catalog.get("bundles", []):
    if bundle["bundle_id"] == bundle_id and "files" in bundle:
      return bundle
  raise CatalogUnavailable(f"no provenance for {bundle_id}")


def download_url(catalog: dict[str, Any], oid: str, api: str = API) -> str:
  record = next((f for f in catalog.get("files", []) if f["oid"] == oid), None)
  repo = catalog.get("release_repo")
  if record and record.get("release") and repo:
    repo = urllib.parse.quote(repo, safe="/")
    release = urllib.parse.quote(record["release"], safe="")
    return f"https://github.com/{repo}/releases/download/{release}/{oid}.onnx"
  if api:
    return f"{api.rstrip('/')}/v1/files/{oid}/download"
  if oid in catalog.get("mirror_unavailable", []):
    raise CatalogUnavailable(f"file {oid} is no longer available upstream")
  raise CatalogUnavailable(f"file {oid} has no recorded download location")


def pull(bundle_id: str, out_dir: Path, api: str = API,
         mirror: str = MIRROR) -> dict[str, Any]:
  """Download a whole bundle and report the constants that must be applied with it."""
  catalog, _ = fetch_catalog(api, mirror)
  record = provenance(bundle_id, catalog, api)

  for entry in record["files"]:
    filename = entry["filename"]
    if Path(filename).name != filename:
      raise ValueError(f"unsafe filename from catalog: {filename!r}")
    target = out_dir / filename
    print(f"  {entry['role']:<12} {entry['filename']}  ({entry['size']/2**20:.0f} MB)")
    download_verified(entry["oid"], target, download_url(catalog, entry["oid"], api))
    print(f"  {'':<12} verified sha256 == oid")

  if missing := record.get("host_constants_missing"):
    print(f"\n  WARNING: constants not found upstream: {', '.join(missing)}", file=sys.stderr)
    print("  They are absent, not zero. Do not substitute defaults.", file=sys.stderr)

  return record


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--api", default=API)
  parser.add_argument("--mirror", default=MIRROR)
  parser.add_argument("--list", action="store_true", help="list installable models")
  parser.add_argument("--pull", metavar="BUNDLE_ID", help="download and verify a bundle")
  parser.add_argument("--out", default="models", type=Path)
  parser.add_argument("--merged-only", action="store_true",
                      help="require at least one merged occurrence")
  args = parser.parse_args()

  allowed = MERGED_ONLY if args.merged_only else ALL_STATUSES

  if args.list:
    catalog, source = fetch_catalog(args.api, args.mirror)
    models = select(catalog, allow_statuses=allowed)
    print(f"{len(models)} models (source: {source}, statuses: {sorted(allowed)})\n")
    for bundle in models[-25:]:
      print(f"  {bundle['bundle_id']}  {bundle['introduced_by']['date'][:10]}  "
            f"{','.join(sorted(statuses(bundle))):<22} {terminal_text(bundle['name'])[:44]}")
    return 0

  if args.pull:
    print(f"pulling {args.pull}")
    record = pull(args.pull, args.out, args.api, args.mirror)
    print("\nRequired companion configuration — apply these with the weights:")
    for key, value in sorted(record.get("host_constants", {}).items()):
      print(f"  {key} = {value}")
    print(f"\n{record['disclaimer']}")
    return 0

  parser.print_help()
  return 1


if __name__ == "__main__":
  sys.exit(main())
