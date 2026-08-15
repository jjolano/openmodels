"""Fetch LFS blobs from comma's public store.

openpilot's .lfsconfig points at gitlab.com/commaai/openpilot-lfs, whose batch API answers
unauthenticated. That is the load-bearing fact behind this whole archive: the entire model
history is reachable without credentials, and pointers give us oid+size up front so nothing is
downloaded twice.

Blobs are verified on arrival. The oid *is* the sha256, so a mismatch is free to detect and
must always abort — this is the same check the reference client performs.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

BATCH_URL = "https://gitlab.com/commaai/openpilot-lfs.git/info/lfs/objects/batch"
LFS_CONTENT_TYPE = "application/vnd.git-lfs+json"
CHUNK = 1 << 20


class LFSError(RuntimeError):
  pass


class VerificationError(LFSError):
  """Downloaded bytes did not hash to the requested oid."""


def verified(path: Path, oid: str) -> bool:
  with open(path, "rb") as handle:
    return hashlib.file_digest(handle, "sha256").hexdigest() == oid


def resolve(oids: list[tuple[str, int]], batch_url: str = BATCH_URL,
            timeout: int = 30, unavailable: set[str] | None = None) -> dict[str, str]:
  """Ask the batch API for download hrefs. Returns {oid: href} for whatever it offers."""
  if not oids:
    return {}

  payload = json.dumps({
    "operation": "download",
    "transfers": ["basic"],
    "objects": [{"oid": oid, "size": size} for oid, size in oids],
  }).encode()

  request = urllib.request.Request(
    batch_url, data=payload,
    headers={"Accept": LFS_CONTENT_TYPE, "Content-Type": LFS_CONTENT_TYPE},
  )
  try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
      body = json.loads(response.read())
  except urllib.error.URLError as exc:
    raise LFSError(f"batch request failed: {exc}") from exc

  hrefs = {}
  for obj in body.get("objects", []):
    # Objects comma has GC'd come back with an "error" member instead of actions.
    href = (obj.get("actions") or {}).get("download", {}).get("href")
    if href:
      hrefs[obj["oid"]] = href
    elif (obj.get("error") or {}).get("code") in (404, 410) and unavailable is not None:
      unavailable.add(obj["oid"])
  return hrefs


def download(oid: str, href: str, dest: Path, timeout: int = 300) -> Path:
  """Stream to disk, hashing as we go, and refuse anything that doesn't match its oid."""
  dest.parent.mkdir(parents=True, exist_ok=True)
  tmp = dest.with_suffix(dest.suffix + ".part")
  digest = hashlib.sha256()

  try:
    with urllib.request.urlopen(href, timeout=timeout) as response, open(tmp, "wb") as handle:
      while chunk := response.read(CHUNK):
        digest.update(chunk)
        handle.write(chunk)

    if digest.hexdigest() != oid:
      raise VerificationError(f"{oid}: got {digest.hexdigest()} — refusing blob")

    tmp.replace(dest)
    return dest
  except Exception:
    tmp.unlink(missing_ok=True)
    raise


def fetch_missing(oids: list[tuple[str, int]], cache_dir: Path,
                  batch_url: str = BATCH_URL, limit: int | None = None,
                  progress=lambda *_: None,
                  unavailable: set[str] | None = None) -> dict[str, Path]:
  """Download any oids not already cached. Returns {oid: path} for everything on disk.

  Batched in groups of 100 so one hostile or GC'd object can't sink a whole run.
  """
  cache_dir.mkdir(parents=True, exist_ok=True)
  have: dict[str, Path] = {}
  wanted: list[tuple[str, int]] = []

  for oid, size in oids:
    path = cache_dir / f"{oid}.onnx"
    if path.exists() and verified(path, oid):
      have[oid] = path
    else:
      path.unlink(missing_ok=True)
      wanted.append((oid, size))

  if unavailable:
    wanted.sort(key=lambda item: item[0] in unavailable)
  if limit is not None:
    wanted = wanted[:limit]
  progress(f"{len(have)} cached, {len(wanted)} to fetch")

  for start in range(0, len(wanted), 100):
    batch = wanted[start:start + 100]
    batch_unavailable: set[str] = set()
    hrefs = resolve(batch, batch_url, unavailable=batch_unavailable)
    if unavailable is not None:
      unavailable.update(batch_unavailable)
    for oid, size in batch:
      href = hrefs.get(oid)
      if not href:
        state = "unavailable upstream" if oid in batch_unavailable else "no download action"
        progress(f"  {state}: {oid[:12]}")
        continue
      try:
        have[oid] = download(oid, href, cache_dir / f"{oid}.onnx")
        progress(f"  fetched {oid[:12]} ({size/2**20:.0f} MB)")
      except (LFSError, urllib.error.URLError, TimeoutError) as exc:
        progress(f"  failed {oid[:12]}: {exc}")

  return have
