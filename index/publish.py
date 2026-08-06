#!/usr/bin/env python3
"""Mirror model blobs to GitHub Releases and record where each one landed.

Why mirror at all: for reverted and never-merged models this archive may be the only public
copy, and comma is free to GC LFS objects for unreachable commits. Releases are free, uncapped
in total size and bandwidth, and allow 2 GiB per asset — comfortably above the 296 MB big
models.

GitHub allows **1000 assets per release**, so blobs are sharded across `blobs-NNNN` releases.
Which shard holds an oid is therefore not derivable from the oid: GitHub is the source of truth
and each file's `release` tag is written back into the index so the API can build a redirect.

Idempotent by construction — existing assets are listed first and never re-uploaded, so a run
that dies halfway is safe to repeat.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from index import lfs

ASSETS_PER_RELEASE = 1000          # GitHub's documented, enforced cap
TAG_PREFIX = "blobs-"


class PublishError(RuntimeError):
  pass


def _gh(*args: str) -> str:
  if shutil.which("gh") is None:
    raise PublishError("the gh CLI is required to publish (it handles auth and multipart upload)")
  result = subprocess.run(["gh", *args], capture_output=True, text=True)
  if result.returncode != 0:
    raise PublishError(f"gh {' '.join(args)}: {result.stderr.strip()}")
  return result.stdout


def _gh_ok(*args: str) -> bool:
  """Whether a gh command succeeded.

  Must be judged by exit code: `gh api` writes its error body to *stdout* on a 404, so testing
  output for truthiness reports every missing release as present.
  """
  if shutil.which("gh") is None:
    raise PublishError("the gh CLI is required to publish")
  return subprocess.run(["gh", *args], capture_output=True, text=True).returncode == 0


def existing_assets(repo: str) -> dict[str, str]:
  """{oid: release_tag} for everything already mirrored.

  Read from GitHub rather than remembered locally: the index is regenerated from scratch each
  run, so anything we "remember" would be lost, and a stale local map would re-upload the world.
  """
  raw = _gh("api", "--paginate", f"repos/{repo}/releases", "--jq",
            ".[] | {tag: .tag_name, assets: [.assets[].name]}")
  mapping: dict[str, str] = {}
  for line in raw.splitlines():
    if not line.strip():
      continue
    entry = json.loads(line)
    if not entry["tag"].startswith(TAG_PREFIX):
      continue
    for name in entry["assets"]:
      if name.endswith(".onnx"):
        mapping[name[: -len(".onnx")]] = entry["tag"]
  return mapping


def ensure_release(repo: str, tag: str) -> None:
  if _gh_ok("api", f"repos/{repo}/releases/tags/{tag}"):
    return
  _gh("release", "create", tag, "--repo", repo, "--title", f"Model blobs {tag}",
      "--notes", "Model weights mirrored from commaai/openpilot. Filenames are their sha256 "
                 "(the git-lfs oid) — verify every download against it.")


def shard_for(counts: dict[str, int], repo: str) -> str:
  """First shard with room, creating one when they're all full."""
  index = 0
  while True:
    tag = f"{TAG_PREFIX}{index:04d}"
    if counts.get(tag, 0) < ASSETS_PER_RELEASE:
      ensure_release(repo, tag)
      counts.setdefault(tag, 0)
      return tag
    index += 1


def publish(index_path: Path, cache_dir: Path, repo: str, limit: int | None = None,
            dry_run: bool = False, keep_blobs: bool = False,
            progress=print) -> dict[str, Any]:
  index = json.loads(index_path.read_text())
  placed = {} if dry_run else existing_assets(repo)

  counts: dict[str, int] = {}
  for tag in placed.values():
    counts[tag] = counts.get(tag, 0) + 1

  pending = [f for f in index["files"] if f["oid"] not in placed]
  progress(f"{len(placed)} already mirrored, {len(pending)} pending")

  uploaded = 0
  failed: list[str] = []
  for record in pending:
    if limit is not None and uploaded >= limit:
      break

    size_mb = record["size"] / 2**20
    if dry_run:
      # Nothing below this point may touch the network or the repo.
      progress(f"  would mirror {record['oid'][:12]} ({size_mb:.0f} MB)")
      placed[record["oid"]] = f"{TAG_PREFIX}0000"
      uploaded += 1
      continue

    # Fetch on demand rather than pre-caching the archive. Only unmirrored blobs are ever
    # downloaded, so steady-state runs move nothing and a discarded cache costs nothing.
    blob = cache_dir / f"{record['oid']}.onnx"
    fetched_now = False
    if not blob.exists():
      got = lfs.fetch_missing([(record["oid"], record["size"])], cache_dir, progress=progress)
      if record["oid"] not in got:
        progress(f"  unavailable upstream: {record['oid'][:12]}")
        continue
      blob, fetched_now = got[record["oid"]], True

    tag = shard_for(counts, repo)
    progress(f"  upload {record['oid'][:12]} ({size_mb:.0f} MB) -> {tag}")
    try:
      _gh("release", "upload", tag, str(blob), "--repo", repo, "--clobber")
    except PublishError as exc:
      # One bad blob must not abandon a multi-hour backfill. The run is idempotent, so a
      # failure is simply retried next time.
      progress(f"  FAILED {record['oid'][:12]}: {exc}")
      failed.append(record["oid"])
      if fetched_now and not keep_blobs:
        blob.unlink(missing_ok=True)
      continue
    counts[tag] = counts.get(tag, 0) + 1
    placed[record["oid"]] = tag
    uploaded += 1

    # Keep the runner's disk bounded: an 8GB archive won't fit alongside a checkout.
    if fetched_now and not keep_blobs:
      blob.unlink(missing_ok=True)

  # Write the shard back onto each file so the API can build a download URL. A file with no
  # release is simply not downloadable yet — the API reports that rather than 302ing to a 404.
  for record in index["files"]:
    if tag := placed.get(record["oid"]):
      record["release"] = tag

  index["release_repo"] = repo
  index["mirrored_count"] = sum(1 for f in index["files"] if f.get("release"))
  index["mirror_failures"] = failed
  if not dry_run:
    index_path.write_text(json.dumps(index, indent=1) + "\n")

  progress(f"{index['mirrored_count']}/{len(index['files'])} files mirrored"
           + (f", {len(failed)} failed (retried next run)" if failed else ""))
  return index


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--index", default="data/index.json", type=Path)
  parser.add_argument("--cache", default="data/blobs", type=Path)
  parser.add_argument("--repo", required=True, help="owner/name to publish releases to")
  parser.add_argument("--limit", type=int, help="cap uploads this run")
  parser.add_argument("--dry-run", action="store_true")
  parser.add_argument("--keep-blobs", action="store_true",
                      help="retain downloaded blobs (default: delete after upload)")
  args = parser.parse_args()

  publish(args.index, args.cache, args.repo, args.limit, args.dry_run,
          args.keep_blobs)
  return 0


if __name__ == "__main__":
  sys.exit(main())
