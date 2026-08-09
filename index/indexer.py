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
    try:
      return self._run(*args).split()
    except GitError:
      return []

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


def read_pointer(data: bytes) -> tuple[str, int] | None:
  """Parse an LFS pointer. Returns None if the blob is a real file, not a pointer.

  Refuses a pointer carrying merge-conflict markers: it holds two competing oids and picking
  the first would silently archive whichever side of a conflict happened to sort first.
  """
  if b"<<<<<<<" in data or b">>>>>>>" in data:
    return None
  if len(LFS_POINTER.findall(data)) > 1:
    return None
  oid = LFS_POINTER.search(data)
  size = LFS_SIZE.search(data)
  if not oid or not size:
    return None
  return oid.group(1).decode(), int(size.group(1))


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
                    release_repo: str | None = None) -> None:
  """Fetch blobs and record what each ONNX declares about itself.

  Descriptive only: input/output shapes, dtypes, opsets, operators, and the slice map. None of
  it is a compatibility claim — identical shapes routinely mean different things (AGENTS.md).
  A model whose metadata cannot be parsed is recorded with its error and still served; the
  blob's identity is what this archive guarantees, not that we understood it.
  """
  from index import lfs, metadata

  wanted = [(oid, record["size"]) for oid, record in sorted(files.items())]
  if source == "releases":
    # Everything is already mirrored, so read from our own copy rather than hammering comma's
    # LFS for a metadata-only pass.
    fetched = fetch_from_releases(wanted, cache_dir, release_repo, limit, progress)
  else:
    fetched = lfs.fetch_missing(wanted, cache_dir, limit=limit, progress=progress)

  for oid, path in fetched.items():
    try:
      files[oid]["metadata"] = metadata.parse(str(path))
    except Exception as exc:
      files[oid]["metadata_error"] = f"{type(exc).__name__}: {exc}"


def fetch_from_releases(wanted, cache_dir: Path, repo: str | None,
                        limit: int | None, progress) -> dict[str, Path]:
  """Pull blobs from our own Releases mirror. Verified against the oid like any other source."""
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
    if path.exists():
      have[oid] = path
    else:
      todo.append(oid)
  if limit:
    todo = todo[:limit]
  progress(f"{len(have)} cached, {len(todo)} to fetch from {repo}")

  for oid in todo:
    asset = ids.get(f"{oid}.onnx")
    if not asset:
      progress(f"  not mirrored: {oid[:12]}")
      continue
    path = cache_dir / f"{oid}.onnx"
    with open(path, "wb") as handle:
      subprocess.run(["gh", "api", f"repos/{repo}/releases/assets/{asset}",
                      "-H", "Accept: application/octet-stream"], stdout=handle, check=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != oid:
      path.unlink(missing_ok=True)
      progress(f"  FAILED verify: {oid[:12]}")
      continue
    have[oid] = path
  return have


def index_repo(repo: Repo, head: str = "HEAD", limit: int | None = None,
               progress=lambda *_: None, blob_cache: Path | None = None,
               download_limit: int | None = None, metadata_source: str = "lfs",
               release_repo: str | None = None) -> dict[str, Any]:
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
  head_oids: set[str] = set()

  # Which oids are live at HEAD — the most useful and directly verifiable status signal.
  for path, blob in repo.onnx_at(head).items():
    if pointer := read_pointer(repo.blob(blob)):
      head_oids.add(pointer[0])

  for number, commit in enumerate(commits, 1):
    if number % 25 == 0:
      progress(f"  {number}/{len(commits)}")
    meta = repo.commit_meta(commit)
    subject = parse_subject(meta["subject"])

    # Group by (kind, variant, family). driving/dmonitoring/nav never share a bundle; the big_
    # family targets different hardware; and a self-contained supercombo is a different
    # architecture from a vision+policy split, even when both exist at a transition commit.
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path, blob in repo.onnx_at(commit).items():
      spec = classify(path)
      if spec is None:
        continue
      kind, variant, role = spec
      pointer = read_pointer(repo.blob(blob))
      if pointer is None:
        continue  # pre-LFS era: real file in tree, not a pointer
      oid, size = pointer
      family = "supercombo" if role == "supercombo" else "split"
      groups[(kind, variant, family)].append(
        {"role": role, "filename": Path(path).name, "path": path, "oid": oid, "size": size}
      )
      files.setdefault(oid, {"oid": oid, "size": size, "filenames": set()})
      files[oid]["filenames"].add(Path(path).name)

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
          "in_head": all(m["oid"] in head_oids for m in members),
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

  if blob_cache is not None:
    attach_metadata(files, blob_cache, download_limit, progress,
                    source=metadata_source, release_repo=release_repo)

  bundle_list = sorted(bundles.values(), key=lambda b: b["occurrences"][0]["date"])
  pairings = compose_mod.attested_pairings(bundle_list, files)

  return {
    "schema": 1,
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "upstream_head": repo.commit_meta(head)["commit"],
    "bundle_count": len(bundles),
    "file_count": len(files),
    # (vision_ckpt, policy_ckpt) pairs that actually shipped upstream. The only sound basis for
    # saying two halves were built for each other -- see index/compose.py.
    "attested_pairings": pairings,
    "bundles": bundle_list,
    "files": sorted(files.values(), key=lambda f: f["oid"]),
  }


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
  args = parser.parse_args()

  repo = Repo(args.repo)
  index = index_repo(repo, args.head, args.limit,
                     progress=lambda m: print(m, file=sys.stderr),
                     blob_cache=args.blob_cache, download_limit=args.download_limit,
                     metadata_source=args.metadata_source,
                     release_repo=args.release_repo)

  out = Path(args.out)
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps(index, indent=1) + "\n")
  print(f"{index['bundle_count']} bundles, {index['file_count']} distinct files -> {out}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
