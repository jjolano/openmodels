#!/usr/bin/env python3
"""Launch-boundary checks for indexing and static publication."""

import hashlib
import os
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from index.constants import extract_from_source  # noqa: E402
from index import lfs  # noqa: E402
from clients.reference import terminal_text  # noqa: E402
from index.indexer import attach_metadata, index_repo, merge_previous, read_pointer  # noqa: E402
from index import publish as publisher  # noqa: E402
from web import render as renderer  # noqa: E402
from web.render import render_detail  # noqa: E402


def _pointer(oid: str, size: int = 123456) -> bytes:
  return (f"version https://git-lfs.github.com/spec/v1\n"
          f"oid sha256:{oid}\nsize {size}\n").encode()


def _bundle(bundle_id: str, oid: str, filename: str = "driving_vision.onnx"):
  occurrence = {"commit": "c1", "date": "2026-01-01T00:00:00Z", "pr": 1,
                "subject": "model (#1)", "name": "model", "is_revert": False,
                "status": "pr_only"}
  return {"bundle_id": bundle_id, "kind": "driving", "variant": "standard",
          "family": "split", "files": [{"role": "vision", "filename": filename,
                                           "path": f"models/{filename}", "oid": oid,
                                           "size": 123456}],
          "occurrences": [occurrence], "in_head": False, "name": "model", "slug": "model",
          "introduced_by": {"commit": "c1", "pr": 1, "date": occurrence["date"]},
          "host_constants": {}, "host_constants_sources": {},
          "host_constants_missing": []}


def test_host_constants_accept_only_finite_numbers():
  hostile = 'LAT_SMOOTH_SECONDS = "</td><script>alert(1)</script>"'
  assert extract_from_source(hostile, {"LAT_SMOOTH_SECONDS"}) == {}
  assert extract_from_source("LAT_SMOOTH_SECONDS = True", {"LAT_SMOOTH_SECONDS"}) == {}
  found = extract_from_source("LAT_SMOOTH_SECONDS = 0.2", {"LAT_SMOOTH_SECONDS"})
  assert found["LAT_SMOOTH_SECONDS"].value == 0.2


def test_reference_client_strips_terminal_controls_from_pr_titles():
  assert terminal_text("safe\x1b]52;clipboard\x07title") == "safe�]52;clipboard�title"


def test_renderer_escapes_defensively_and_uses_recorded_release():
  oid = "a" * 64
  bundle = _bundle("b1", oid)
  bundle["host_constants"] = {"LAT_SMOOTH_SECONDS": "</td><script>alert(1)</script>"}
  page = render_detail({"files": [{"oid": oid, "release": "blobs-0000"}],
                        "release_repo": "owner/repo", "attested_pairings": []}, bundle)
  assert "<script>alert(1)</script>" not in page
  assert "&lt;/td&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in page
  assert f"https://github.com/owner/repo/releases/download/blobs-0000/{oid}.onnx" in page


def test_local_renderer_links_only_files_present_in_the_mirror():
  oid = "a" * 64
  bundle = _bundle("b1", oid)
  old_backend = renderer.BLOB_BACKEND
  renderer.BLOB_BACKEND = "local"
  try:
    page = render_detail({"files": [{"oid": oid}], "attested_pairings": []}, bundle)
    assert "mirror pending" in page and f"../blobs/{oid}.onnx" not in page
    page = render_detail({"files": [{"oid": oid, "local_mirrored": True}],
                          "attested_pairings": []}, bundle)
    assert f"../blobs/{oid}.onnx" in page
  finally:
    renderer.BLOB_BACKEND = old_backend


def test_previous_catalog_is_append_only_but_current_observations_win():
  old_oid, new_oid = "a" * 64, "b" * 64
  old_bundle = _bundle("old", old_oid)
  previous = {"bundles": [old_bundle], "files": [{"oid": old_oid, "size": 123456,
               "filenames": ["driving_vision.onnx"], "release": "blobs-0000",
               "metadata": {"lineage": {"self": "vision/1"}}}],
              "release_repo": "owner/repo"}
  current_bundle = _bundle("old", old_oid)
  current_bundle["occurrences"][0]["status"] = "merged"
  current_bundle["in_head"] = True
  current = {"bundles": [current_bundle, _bundle("new", new_oid)],
             "files": [{"oid": old_oid, "size": 123456,
                        "filenames": ["driving_on_policy.onnx"]},
                       {"oid": new_oid, "size": 123456,
                        "filenames": ["driving_vision.onnx"]}],
             "release_repo": "owner/current"}
  merged = merge_previous(current, previous)
  files = {f["oid"]: f for f in merged["files"]}
  bundles = {b["bundle_id"]: b for b in merged["bundles"]}
  assert files[old_oid]["release"] == "blobs-0000"
  assert files[old_oid]["metadata"]["lineage"]["self"] == "vision/1"
  assert files[old_oid]["filenames"] == ["driving_on_policy.onnx", "driving_vision.onnx"]
  assert bundles["old"]["in_head"] is True
  assert bundles["old"]["occurrences"][0]["status"] == "merged"
  assert merged["release_repo"] == "owner/current"


def test_pointer_size_is_bounded_before_integer_conversion():
  assert read_pointer(_pointer("a" * 64)) == ("a" * 64, 123456)
  assert read_pointer(_pointer("a" * 64, 2 * 1024**3)) is None
  hostile = b"oid sha256:" + b"a" * 64 + b"\nsize " + b"9" * 100_000
  assert read_pointer(hostile) is None


def test_metadata_pass_refuses_multi_gigabyte_allocations():
  oid = "a" * 64
  files = {oid: {"oid": oid, "size": 1024**3, "filenames": ["driving_vision.onnx"]}}
  with tempfile.TemporaryDirectory() as tmp:
    attach_metadata(files, Path(tmp), None, lambda *_: None)
  assert files[oid]["metadata_error"].startswith("refused to parse")


def test_lfs_rejects_a_corrupt_cached_blob():
  oid = "a" * 64
  with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)
    (cache / f"{oid}.onnx").write_bytes(b"not the requested blob")
    old_resolve = lfs.resolve
    lfs.resolve = lambda *_, **__: {}
    try:
      result = lfs.fetch_missing([(oid, 22)], cache)
    finally:
      lfs.resolve = old_resolve
    assert oid not in result
    assert not (cache / f"{oid}.onnx").exists()


def test_local_mirror_revalidates_cached_blobs_even_after_metadata_exists():
  oid = hashlib.sha256(b"good").hexdigest()
  with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)
    path = cache / f"{oid}.onnx"
    path.write_bytes(b"corrupt")
    files = {oid: {"oid": oid, "size": 4, "filenames": ["driving_vision.onnx"],
                   "metadata": {}}}
    old_resolve = lfs.resolve
    lfs.resolve = lambda *_, **__: {}
    try:
      attach_metadata(files, cache, None, lambda *_: None, fetch_all_missing=True)
    finally:
      lfs.resolve = old_resolve
    assert not path.exists(), "a corrupt cached final must not be advertised by the local mirror"


def test_interrupted_lfs_download_removes_partial_file():
  class BrokenResponse:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, _): raise TimeoutError("connection dropped")

  with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp) / "model.onnx"
    old_urlopen = lfs.urllib.request.urlopen
    lfs.urllib.request.urlopen = lambda *_, **__: BrokenResponse()
    try:
      try:
        lfs.download("a" * 64, "https://example.invalid", dest)
        raise AssertionError("interrupted download must fail")
      except TimeoutError:
        pass
    finally:
      lfs.urllib.request.urlopen = old_urlopen
    assert not dest.with_suffix(".onnx.part").exists()


def test_only_confirmed_missing_lfs_objects_are_marked_unavailable():
  class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps(self.body).encode()

  oid = "a" * 64
  old_urlopen = lfs.urllib.request.urlopen
  try:
    gone = set()
    lfs.urllib.request.urlopen = lambda *_, **__: Response(
      {"objects": [{"oid": oid, "error": {"code": 500, "message": "retry"}}]})
    lfs.resolve([(oid, 1)], unavailable=gone)
    assert not gone
    lfs.urllib.request.urlopen = lambda *_, **__: Response(
      {"objects": [{"oid": oid, "error": {"code": 404, "message": "gone"}}]})
    lfs.resolve([(oid, 1)], unavailable=gone)
    assert gone == {oid}
  finally:
    lfs.urllib.request.urlopen = old_urlopen


def test_publisher_rejects_a_corrupt_preexisting_cache_file():
  oid = hashlib.sha256(b"good").hexdigest()
  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    path = root / "index.json"
    cache = root / "cache"
    cache.mkdir()
    (cache / f"{oid}.onnx").write_bytes(b"corrupt")
    path.write_text(json.dumps({"files": [{"oid": oid, "size": 4,
                                             "filenames": ["driving_vision.onnx"]}],
                                "bundles": [], "attested_pairings": []}))
    old_assets, old_fetch = publisher.existing_assets, publisher.lfs.fetch_missing
    publisher.existing_assets = lambda _: {}
    publisher.lfs.fetch_missing = lambda *_, **__: {}
    try:
      result = publisher.publish(path, cache, "owner/repo")
    finally:
      publisher.existing_assets, publisher.lfs.fetch_missing = old_assets, old_fetch
    assert result["mirror_failures"] == [oid]
    assert not (cache / f"{oid}.onnx").exists()


def test_known_unavailable_blobs_do_not_starve_local_backfill():
  gone, fresh = "a" * 64, "b" * 64
  attempted = []

  def fake_resolve(batch, *_, **__):
    attempted.extend(oid for oid, _ in batch)
    return {}

  with tempfile.TemporaryDirectory() as tmp:
    old_resolve = lfs.resolve
    lfs.resolve = fake_resolve
    try:
      lfs.fetch_missing([(gone, 1), (fresh, 1)], Path(tmp), limit=1,
                        unavailable={gone})
    finally:
      lfs.resolve = old_resolve
  assert attempted == [fresh]


def test_in_head_uses_bundle_identity_not_oid_alone():
  oid_v, oid_p = "a" * 64, "b" * 64

  class FakeRepo:
    def exists(self, _): return True
    def commits_touching(self, _): return ["c1"]
    def commit_meta(self, ref):
      return {"commit": ref, "date": "2026-01-01T00:00:00Z", "subject": "model (#1)"}
    def onnx_at(self, ref):
      policy = "driving_on_policy.onnx" if ref == "HEAD" else "driving_policy.onnx"
      return {"models/driving_vision.onnx": "v", f"models/{policy}": "p"}
    def blob(self, blob): return _pointer(oid_v if blob == "v" else oid_p)
    def is_ancestor(self, *_): return True
    def show(self, *_): return None

  catalog = index_repo(FakeRepo(), progress=lambda *_: None)
  assert len(catalog["bundles"]) == 1
  assert catalog["bundles"][0]["in_head"] is False
  assert all(f["host_contexts"] for f in catalog["files"])


def test_publisher_distinguishes_gone_blobs_from_pending_backfill():
  oid = "a" * 64

  def unavailable_fetch(*_, unavailable=None, **__):
    unavailable.add(oid)
    return {}

  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "index.json"
    path.write_text(json.dumps({"files": [{"oid": oid, "size": 123456,
                                           "filenames": ["driving_vision.onnx"]}],
                                "bundles": [], "attested_pairings": []}))
    old_assets, old_fetch = publisher.existing_assets, publisher.lfs.fetch_missing
    publisher.existing_assets = lambda _: {}
    publisher.lfs.fetch_missing = unavailable_fetch
    try:
      result = publisher.publish(path, Path(tmp) / "cache", "owner/repo")
    finally:
      publisher.existing_assets, publisher.lfs.fetch_missing = old_assets, old_fetch
  assert result["mirror_unavailable"] == [oid]
  assert result["mirror_failures"] == []
  assert result["mirrored_count"] == 0

  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "index.json"
    path.write_text(json.dumps({"files": [{"oid": oid, "size": 123456,
                                           "filenames": ["driving_vision.onnx"]}],
                                "bundles": [], "attested_pairings": []}))
    old_assets, old_fetch = publisher.existing_assets, publisher.lfs.fetch_missing
    publisher.existing_assets = lambda _: {}
    publisher.lfs.fetch_missing = lambda *_, **__: {}
    try:
      result = publisher.publish(path, Path(tmp) / "cache", "owner/repo")
    finally:
      publisher.existing_assets, publisher.lfs.fetch_missing = old_assets, old_fetch
  assert result["mirror_unavailable"] == []
  assert result["mirror_failures"] == [oid]


if __name__ == "__main__":
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  for test in tests:
    test()
    print(f"  ok  {test.__name__}")
  print(f"\n{len(tests)} passed")
