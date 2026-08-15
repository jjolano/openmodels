#!/usr/bin/env python3
"""Index openpilot driving models from git history into a provenance catalog.

Detection is pure git: no GitHub API, no token. Model files are LFS pointers (133 bytes), so
the entire history is enumerable without downloading a single blob.

What this produces is identity and provenance. It renders no compatibility verdict — see
AGENTS.md for why structural similarity is not interchangeability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from index import compose as compose_mod
from index import constants as host_constants

# Three path eras. Models lived at the repo root until 2022, then under selfdrive/, then were
# moved wholesale under openpilot/ by #38223. Missing an era silently loses models.
MODEL_DIRS = (
  "models",
  "selfdrive/modeld/models",
  "openpilot/selfdrive/modeld/models",
)

# filename stem -> (kind, variant, role). Order matters: `big_` prefixes are checked first.
ROLE_PATTERNS: tuple[tuple[str, tuple[str, str, str]], ...] = (
  ("big_driving_vision",      ("driving", "big", "vision")),
  ("big_driving_on_policy",   ("driving", "big", "on_policy")),
  ("big_driving_off_policy",  ("driving", "big", "off_policy")),
  ("big_driving_policy",      ("driving", "big", "on_policy")),
  ("big_driving_supercombo",  ("driving", "big", "supercombo")),
  ("driving_vision",          ("driving", "standard", "vision")),
  ("driving_on_policy",       ("driving", "standard", "on_policy")),
  ("driving_off_policy",      ("driving", "standard", "off_policy")),
  ("driving_policy",          ("driving", "standard", "on_policy")),
  ("driving_supercombo",      ("driving", "standard", "supercombo")),
  ("supercombo",              ("driving", "standard", "supercombo")),
  ("dmonitoring_model",       ("dmonitoring", "standard", "dmonitoring")),
  ("navmodel",                ("nav", "standard", "navmodel")),
)

LFS_POINTER = re.compile(rb"^oid sha256:([0-9a-f]{64})$", re.M)
LFS_SIZE = re.compile(rb"^size (\d+)$", re.M)
# "Revert "Op model16 deep (#38073)" (#38166)" -> reverted PR 38073, via PR 38166
REVERT_RE = re.compile(r'^Revert\s+"(?P<title>.*?)(?:\s*\(#(?P<orig>\d+)\))?"\s*(?:\(#(?P<via>\d+)\))?')
PR_RE = re.compile(r"\(#(\d+)\)\s*$")


class GitError(RuntimeError):
  pass


class Repo:
  def __init__(self, path: str):
    self.path = str(Path(path).resolve())

  def _run(self, *args: str, binary: bool = False) -> Any:
    result = subprocess.run(
      ["git", "-C", self.path, *args],
      capture_output=True, text=not binary,
    )
    if result.returncode != 0:
      err = result.stderr if not binary else result.stderr.decode("utf-8", "replace")
      raise GitError(f"git {' '.join(args)}: {err.strip()}")
    return result.stdout

  def exists(self, ref: str) -> bool:
    try:
      self._run("cat-file", "-e", f"{ref}^{{commit}}")
      return True
    except GitError:
      return False

  def show(self, ref: str, path: str) -> str | None:
    try:
      return self._run("show", f"{ref}:{path}")
    except GitError:
      return None

  def show_bytes(self, ref: str, path: str) -> bytes | None:
    try:
      return self._run("show", f"{ref}:{path}", binary=True)
    except GitError:
      return None

  def commits_touching(self, dirs: Iterable[str], all_refs: bool = True) -> list[str]:
    args = ["log", "--format=%H"]
    if all_refs:
      args.append("--all")
    args += ["--", *[f"{d}/*.onnx" for d in dirs]]
    return self._run(*args).split()

  def commit_meta(self, ref: str) -> dict[str, str]:
    out = self._run("show", "-s", "--format=%H%x00%cI%x00%s", ref).split("\0")
    return {"commit": out[0], "date": out[1], "subject": out[2].strip()}

  def onnx_at(self, ref: str) -> dict[str, str]:
    """{path: blob_sha} for every .onnx under any known model dir at this commit."""
    found: dict[str, str] = {}
    for directory in MODEL_DIRS:
      try:
        listing = self._run("ls-tree", "-r", "--format=%(objectname) %(path)", ref, directory)
      except GitError:
        continue
      for line in listing.splitlines():
        if not line.strip():
          continue
        blob, _, path = line.partition(" ")
        if path.endswith(".onnx"):
          found[path] = blob
    return found

  def blob(self, sha: str) -> bytes:
    return self._run("cat-file", "-p", sha, binary=True)

  def is_ancestor(self, ref: str, of: str) -> bool:
    try:
      subprocess.run(["git", "-C", self.path, "merge-base", "--is-ancestor", ref, of],
                     capture_output=True, check=True)
      return True
    except subprocess.CalledProcessError:
      return False


def classify(path: str) -> tuple[str, str, str] | None:
  """path -> (kind, variant, role), or None if it isn't a model we recognise."""
  stem = Path(path).stem
  for prefix, spec in ROLE_PATTERNS:
    if stem == prefix:
      return spec
  return None


# An ONNX model is never this small. Upstream has at least two LFS objects that are actually
# git conflict debris (a pointer file with <<<<<<< markers, stored verbatim as an object), and
# they mirror perfectly while being useless as models. Flag rather than drop: the archive's job
# is to faithfully record what upstream contained.
SUSPECT_MAX_BYTES = 100_000
# Current USBGPU models top out around 296 MB. Parsing reads one protobuf into memory, so keep a
# hostile PR from turning a metadata pass into a multi-gigabyte allocation.
MAX_METADATA_BYTES = 512 * 1024**2


def read_pointer(data: bytes) -> tuple[str, int] | None:
  """Parse an LFS pointer. Returns None if the blob is a real file, not a pointer.

  Refuses a pointer carrying merge-conflict markers: it holds two competing oids and picking
  the first would silently archive whichever side of a conflict happened to sort first.
  """
  if len(data) > 4096 or b"<<<<<<<" in data or b">>>>>>>" in data:
    return None
  if len(LFS_POINTER.findall(data)) > 1:
    return None
  oid = LFS_POINTER.search(data)
  size = LFS_SIZE.search(data)
  if not oid or not size:
    return None
  # GitHub cannot mirror a 2 GiB asset, and bounding the decimal before int() keeps a hostile PR
  # from using an absurdly long size field to crash the indexer.
  raw_size = size.group(1)
  if len(raw_size) > 10:
    return None
  parsed_size = int(raw_size)
  if not 0 < parsed_size < 2 * 1024**3:
    return None
  return oid.group(1).decode(), parsed_size


def bundle_id(members: list[dict[str, Any]]) -> str:
  """Content-address a bundle over (role, filename, oid).

  Roles and filenames are included deliberately: commit 249cafe renamed driving_policy to
  driving_on_policy without changing content, and that rename carries runtime meaning.
  """
  key = sorted((m["role"], m["filename"], m["oid"]) for m in members)
  return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def parse_subject(subject: str) -> dict[str, Any]:
  info: dict[str, Any] = {"pr": None, "reverts_pr": None, "name": subject}
  if match := PR_RE.search(subject):
    info["pr"] = int(match.group(1))
    info["name"] = subject[: match.start()].strip()
  if revert := REVERT_RE.match(subject):
    info["reverts_pr"] = int(revert.group("orig")) if revert.group("orig") else None
    info["name"] = revert.group("title").strip()
    info["is_revert"] = True
  return info


def slugify(name: str) -> str:
  return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


def attach_metadata(files: dict[str, dict[str, Any]], cache_dir: Path,
                    limit: int | None, progress, source: str = "lfs",
                    release_repo: str | None = None,
                    fetch_all_missing: bool = False,
                    known_unavailable: set[str] | None = None) -> set[str]:
  """Fetch blobs and record what each ONNX declares about itself.

  Descriptive only: input/output shapes, dtypes, opsets, operators, and the slice map. None of
  it is a compatibility claim — identical shapes routinely mean different things (AGENTS.md).
  A model whose metadata cannot be parsed is recorded with its error and still served; the
  blob's identity is what this archive guarantees, not that we understood it.
  """
  from index import lfs, metadata

  for record in files.values():
    if record["size"] > MAX_METADATA_BYTES:
      record["metadata_error"] = (
        f"refused to parse {record['size']} bytes (limit {MAX_METADATA_BYTES})"
      )
  wanted = [
    (oid, record["size"])
    for oid, record in sorted(files.items())
    if fetch_all_missing or ("metadata" not in record and "metadata_error" not in record)
  ]
  unavailable = set(known_unavailable or ())
  if source == "releases":
    # Everything is already mirrored, so read from our own copy rather than hammering comma's
    # LFS for a metadata-only pass.
    fetched = fetch_from_releases(wanted, cache_dir, release_repo, limit, progress)
  else:
    fetched = lfs.fetch_missing(wanted, cache_dir, limit=limit, progress=progress,
                                unavailable=unavailable)

  for oid, path in fetched.items():
    if "metadata" in files[oid] or "metadata_error" in files[oid]:
      continue
    try:
      files[oid]["metadata"] = metadata.parse(str(path))
    except Exception as exc:
      files[oid]["metadata_error"] = f"{type(exc).__name__}: {exc}"
  return unavailable


def fetch_from_releases(wanted, cache_dir: Path, repo: str | None,
                        limit: int | None, progress) -> dict[str, Path]:
  """Pull blobs from our own Releases mirror. Verified against the oid like any other source."""
  from index import lfs

  if not repo:
    raise GitError("--metadata-source releases needs --release-repo")
  cache_dir.mkdir(parents=True, exist_ok=True)
  # Must raise, never return empty: an auth or network failure here is indistinguishable
  # downstream from "nothing is mirrored", and the run would report every model as lacking
  # metadata while exiting 0.
  try:
    listing = subprocess.run(["gh", "api", "--paginate", f"repos/{repo}/releases", "--jq",
                              ".[].assets[] | \"\\(.name) \\(.id)\""],
                             capture_output=True, text=True, check=True)
  except subprocess.CalledProcessError as exc:
    raise GitError(f"gh api failed listing releases for {repo}: {exc.stderr}") from exc
  # rsplit: the id is the last field, so an asset name containing a space cannot break the parse.
  ids = dict(line.rsplit(None, 1) for line in listing.stdout.splitlines() if line.strip())

  have: dict[str, Path] = {}
  todo = []
  for oid, _ in wanted:
    path = cache_dir / f"{oid}.onnx"
    if path.exists() and lfs.verified(path, oid):
      have[oid] = path
    else:
      path.unlink(missing_ok=True)
      if f"{oid}.onnx" in ids:
        todo.append(oid)
      else:
        progress(f"  not mirrored: {oid[:12]}")
  if limit:
    todo = todo[:limit]
  progress(f"{len(have)} cached, {len(todo)} to fetch from {repo}")

  for oid in todo:
    asset = ids[f"{oid}.onnx"]
    path = cache_dir / f"{oid}.onnx"
    with open(path, "wb") as handle:
      subprocess.run(["gh", "api", f"repos/{repo}/releases/assets/{asset}",
                      "-H", "Accept: application/octet-stream"], stdout=handle, check=True)
    if not lfs.verified(path, oid):
      path.unlink(missing_ok=True)
      progress(f"  FAILED verify: {oid[:12]}")
      continue
    have[oid] = path
  return have


def merge_previous(index: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
  """Retain records whose only upstream ref was force-pushed or deleted.

  Current observations win for mutable facts; old occurrences, metadata, and release placement
  remain append-only. This is the persistence layer the archive needs, and a JSON file is enough.
  """
  files = {f["oid"]: dict(f) for f in previous.get("files", [])}
  for current in index["files"]:
    old = files.get(current["oid"], {})
    merged = {**old, **current}
    merged["filenames"] = sorted(set(old.get("filenames", ())) |
                                 set(current.get("filenames", ())))
    files[current["oid"]] = merged

  bundles = {b["bundle_id"]: {**b, "in_head": False, "upstream_reachable": False}
             for b in previous.get("bundles", [])}
  for current in index["bundles"]:
    current = {**current, "upstream_reachable": True}
    old = bundles.get(current["bundle_id"])
    if old:
      occurrences = {o["commit"]: o for o in old.get("occurrences", [])}
      occurrences.update({o["commit"]: o for o in current.get("occurrences", [])})
      current = {**old, **current,
                 "occurrences": sorted(occurrences.values(), key=lambda o: o["date"])}
    bundles[current["bundle_id"]] = current

  index["files"] = sorted(files.values(), key=lambda f: f["oid"])
  index["bundles"] = sorted(bundles.values(), key=lambda b: b["occurrences"][0]["date"])
  index["file_count"] = len(files)
  index["bundle_count"] = len(bundles)
  for field in ("release_repo", "mirror_unavailable", "mirror_failures"):
    if field not in index and field in previous:
      index[field] = previous[field]
  return index


def attach_host_contexts(index: dict[str, Any]) -> None:
  """Put each driving half's recorded host configuration beside its content record.

  Composition selects files by oid, not bundles. Keeping every upstream context prevents it
  from silently borrowing constants from an unrelated half or hiding genuine ambiguity.
  """
  contexts: dict[str, list[dict[str, Any]]] = defaultdict(list)
  for bundle in index["bundles"]:
    if bundle["kind"] != "driving":
      continue
    for member in bundle["files"]:
      context = {
        "role": member["role"],
        "bundle_id": bundle["bundle_id"],
        "commit": bundle["introduced_by"]["commit"],
        "host_constants": bundle.get("host_constants", {}),
        "host_constants_sources": bundle.get("host_constants_sources", {}),
        "host_constants_missing": bundle.get("host_constants_missing", []),
      }
      if "frame_skip" in bundle:
        context["frame_skip"] = bundle["frame_skip"]
      if context not in contexts[member["oid"]]:
        contexts[member["oid"]].append(context)

  for record in index["files"]:
    record["host_contexts"] = contexts.get(record["oid"], [])


def index_repo(repo: Repo, head: str = "HEAD", limit: int | None = None,
               progress=lambda *_: None, blob_cache: Path | None = None,
               download_limit: int | None = None, metadata_source: str = "lfs",
               release_repo: str | None = None, previous: dict[str, Any] | None = None,
               mirror_local: bool = False) -> dict[str, Any]:
  if not repo.exists(head):
    raise GitError(f"ref {head!r} is not reachable — refusing to index (would silently "
                   f"report every model as missing)")

  commits = repo.commits_touching(MODEL_DIRS)
  progress(f"{len(commits)} commits touch model dirs")
  if limit:
    commits = commits[:limit]

  bundles: dict[str, dict[str, Any]] = {}
  files: dict[str, dict[str, Any]] = {}
  pr_to_bundles: dict[int, set[str]] = defaultdict(set)

  def groups_at(ref: str) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path, blob in repo.onnx_at(ref).items():
      spec = classify(path)
      pointer = read_pointer(repo.blob(blob)) if spec else None
      if spec is None or pointer is None:
        continue
      kind, variant, role = spec
      oid, size = pointer
      family = "supercombo" if role == "supercombo" else "split"
      groups[(kind, variant, family)].append(
        {"role": role, "filename": Path(path).name, "path": path, "oid": oid, "size": size}
      )
    return groups

  head_groups = groups_at(head)
  if not any(kind == "driving" for kind, _, _ in head_groups):
    raise GitError("no recognised driving model at upstream HEAD — refusing to publish a "
                   "silently stale catalog; check MODEL_DIRS and ROLE_PATTERNS")
  head_bundle_ids = {bundle_id(members) for members in head_groups.values()}

  for number, commit in enumerate(commits, 1):
    if number % 25 == 0:
      progress(f"  {number}/{len(commits)}")
    meta = repo.commit_meta(commit)
    subject = parse_subject(meta["subject"])

    # Group by (kind, variant, family). driving/dmonitoring/nav never share a bundle; the big_
    # family targets different hardware; and a self-contained supercombo is a different
    # architecture from a vision+policy split, even when both exist at a transition commit.
    groups = groups_at(commit)
    for members in groups.values():
      for member in members:
        oid = member["oid"]
        files.setdefault(oid, {"oid": oid, "size": member["size"], "filenames": set()})
        files[oid]["filenames"].add(member["filename"])

    for (kind, variant, family), members in groups.items():
      bid = bundle_id(members)
      occurrence = {
        "commit": commit,
        "date": meta["date"],
        "pr": subject["pr"],
        "subject": meta["subject"],
        "name": subject["name"],
        "is_revert": bool(subject.get("is_revert")),
        "status": "merged" if repo.is_ancestor(commit, head) else "pr_only",
      }
      if bid not in bundles:
        bundles[bid] = {
          "bundle_id": bid,
          "kind": kind,
          "variant": variant,
          "family": family,
          "files": sorted(members, key=lambda m: m["role"]),
          "occurrences": [],
          "in_head": bid in head_bundle_ids,
          "upstream_reachable": True,
        }
      # Occurrences accrue: the same content can land, be reverted, and re-land.
      if commit not in {o["commit"] for o in bundles[bid]["occurrences"]}:
        bundles[bid]["occurrences"].append(occurrence)
      if subject["pr"]:
        pr_to_bundles[subject["pr"]].add(bid)

  # Second pass: link reverts. A revert commit names the PR it undoes, so the bundles that PR
  # introduced get a reverted occurrence. We record the linkage and never guess at the reason.
  for commit in commits:
    meta = repo.commit_meta(commit)
    subject = parse_subject(meta["subject"])
    if not subject.get("is_revert") or not subject["reverts_pr"]:
      continue
    for bid in pr_to_bundles.get(subject["reverts_pr"], ()):
      for occurrence in bundles[bid]["occurrences"]:
        if occurrence["pr"] == subject["reverts_pr"]:
          occurrence["status"] = "reverted"
          occurrence["reverted_by_commit"] = commit
          occurrence["reverted_at"] = meta["date"]

  for bundle in bundles.values():
    bundle["occurrences"].sort(key=lambda o: o["date"])

    # Name the bundle after the commit that introduced this file set. A revert commit restores
    # an older set, so it never names anything — otherwise bundles end up called "Revert OP".
    introduced = next((o for o in bundle["occurrences"] if not o["is_revert"]),
                      bundle["occurrences"][0])
    bundle["name"] = introduced["name"]
    bundle["slug"] = slugify(introduced["name"])
    bundle["introduced_by"] = {"commit": introduced["commit"], "pr": introduced["pr"],
                               "date": introduced["date"]}

    # Host constants come from the introducing commit — they are the compatibility payload,
    # and only for driving models. Smoothing constants are meaningless for DM or nav.
    if bundle["kind"] != "driving":
      continue
    extracted = host_constants.extract(lambda p, c=introduced["commit"]: repo.show(c, p))
    bundle["host_constants"] = extracted["values"]
    bundle["host_constants_sources"] = extracted["sources"]
    bundle["host_constants_missing"] = extracted["missing"]
    if "frame_skip" in extracted:
      bundle["frame_skip"] = extracted["frame_skip"]

  for record in files.values():
    record["filenames"] = sorted(record["filenames"])
    if record["size"] < SUSPECT_MAX_BYTES:
      record["suspect"] = "too small to be a model; likely upstream conflict debris"

  bundle_list = sorted(bundles.values(), key=lambda b: b["occurrences"][0]["date"])
  index = {
    "schema": 2,
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "upstream_head": repo.commit_meta(head)["commit"],
    "bundle_count": len(bundles),
    "file_count": len(files),
    # (vision_ckpt, policy_ckpt) pairs that actually shipped upstream. The only sound basis for
    # saying two halves were built for each other -- see index/compose.py.
    "attested_pairings": [],
    "bundles": bundle_list,
    "files": sorted(files.values(), key=lambda f: f["oid"]),
  }
  if previous:
    index = merge_previous(index, previous)
  attach_host_contexts(index)

  files = {f["oid"]: f for f in index["files"]}
  if blob_cache is not None:
    unavailable = attach_metadata(files, blob_cache, download_limit, progress,
                                  source=metadata_source, release_repo=release_repo,
                                  fetch_all_missing=mirror_local,
                                  known_unavailable=set(index.get("mirror_unavailable", ())))
    if mirror_local:
      known_unavailable = set(index.get("mirror_unavailable", ())) | unavailable
      known_unavailable.difference_update(
        oid for oid in known_unavailable if (blob_cache / f"{oid}.onnx").exists()
      )
      index["mirror_unavailable"] = sorted(known_unavailable)
      for oid, record in files.items():
        record["local_mirrored"] = (blob_cache / f"{oid}.onnx").is_file()
      index["mirrored_count"] = sum(f["local_mirrored"] for f in files.values())
  index["attested_pairings"] = compose_mod.attested_pairings(index["bundles"], files)
  index["files"] = sorted(files.values(), key=lambda f: f["oid"])
  return index


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--repo", required=True, help="path to an openpilot clone")
  parser.add_argument("--head", default="HEAD")
  parser.add_argument("--limit", type=int, help="index only the N most recent commits")
  parser.add_argument("--out", default="data/index.json")
  parser.add_argument("--blob-cache", type=Path,
                      help="download blobs here and extract ONNX metadata from them")
  parser.add_argument("--download-limit", type=int,
                      help="cap how many new blobs to fetch this run")
  parser.add_argument("--metadata-source", choices=("lfs", "releases"), default="lfs",
                      help="where to read blobs for metadata extraction")
  parser.add_argument("--release-repo", help="owner/name holding the mirror (for 'releases')")
  parser.add_argument("--previous", type=Path,
                      help="previous index.json; retains records from refs that disappeared")
  parser.add_argument("--mirror-local", action="store_true",
                      help="ensure every blob is present in --blob-cache, not only metadata gaps")
  args = parser.parse_args()

  repo = Repo(args.repo)
  previous = json.loads(args.previous.read_text()) if args.previous and args.previous.exists() else None
  index = index_repo(repo, args.head, args.limit,
                     progress=lambda m: print(m, file=sys.stderr),
                     blob_cache=args.blob_cache, download_limit=args.download_limit,
                     metadata_source=args.metadata_source,
                     release_repo=args.release_repo, previous=previous,
                     mirror_local=args.mirror_local)

  out = Path(args.out)
  out.parent.mkdir(parents=True, exist_ok=True)
  tmp = out.with_suffix(out.suffix + ".tmp")
  tmp.write_text(json.dumps(index, indent=1) + "\n")
  tmp.replace(out)
  print(f"{index['bundle_count']} bundles, {index['file_count']} distinct files -> {out}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
