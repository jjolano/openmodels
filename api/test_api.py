#!/usr/bin/env python3
"""Small end-to-end check for the public API's download states."""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402
from api import main  # noqa: E402


def test_status_and_download_states():
  oid, gone = "a" * 64, "b" * 64
  data = {"schema": 1, "generated_at": "2026-01-01T00:00:00Z", "upstream_head": "c" * 40,
          "bundle_count": 0, "file_count": 2, "bundles": [], "attested_pairings": [],
          "release_repo": "owner/repo", "mirrored_count": 1,
          "mirror_unavailable": [gone], "mirror_failures": [],
          "files": [{"oid": oid, "size": 1, "filenames": ["a.onnx"],
                     "release": "blobs-0000"},
                    {"oid": gone, "size": 1, "filenames": ["b.onnx"]}]}
  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    index = root / "index.json"
    index.write_text(json.dumps(data))
    main.INDEX_PATH = index
    main.LOCAL_BLOB_DIR = root / "blobs"
    main._cache.update(index=None, loaded_at=0.0, mtime=0.0)
    main.BLOB_BACKEND = "github"
    status = main.status()
    assert status["mirror_unavailable_count"] == 1
    redirect = main.download_file(oid)
    assert redirect.status_code == 302 and "blobs-0000" in redirect.headers["location"]
    try:
      main.download_file(gone)
      raise AssertionError("gone upstream blob must not look pending")
    except HTTPException as exc:
      assert exc.status_code == 410

    main.BLOB_BACKEND = "local"
    try:
      main.download_file(oid)
      raise AssertionError("missing local blob must not redirect")
    except HTTPException as exc:
      assert exc.status_code == 503
    main.LOCAL_BLOB_DIR.mkdir()
    (main.LOCAL_BLOB_DIR / f"{oid}.onnx").write_bytes(b"x")
    assert main.download_file(oid).status_code == 302


def test_occurrence_statuses_and_attestation_are_not_collapsed():
  bundle = {
    "bundle_id": "bundle", "name": "model", "slug": "model", "kind": "driving",
    "family": "split", "variant": "standard", "in_head": True,
    "introduced_by": {"commit": "1" * 40, "pr": 1, "date": "2026-01-01T00:00:00Z"},
    "host_constants": {}, "host_constants_sources": {}, "host_constants_missing": [],
    "files": [],
    "occurrences": [
      {"commit": "1" * 40, "date": "2026-01-01T00:00:00Z", "status": "merged"},
      {"commit": "2" * 40, "date": "2026-02-01T00:00:00Z", "status": "pr_only"},
    ],
  }
  data = {"schema": 2, "generated_at": "2026-02-01T00:00:00Z",
          "upstream_head": "3" * 40, "bundle_count": 1, "file_count": 0,
          "bundles": [bundle], "files": [], "attested_pairings": []}
  main._cache.update(index=data, loaded_at=float("inf"), mtime=0.0)
  args = dict(kind=None, family=None, variant=None, role=None, in_head=None, since=None,
              limit=200, offset=0)
  listed = main.list_models(status="merged", **args)
  assert listed["bundles"][0]["statuses"] == ["merged", "pr_only"]
  assert main.list_models(status="pr_only", **args)["count"] == 1
  assert main.get_provenance("bundle")["attested"] is True
  bundle["occurrences"] = [bundle["occurrences"][1]]
  assert main.get_provenance("bundle")["attested"] is False


if __name__ == "__main__":
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  for test in tests:
    test()
    print(f"  ok  {test.__name__}")
  print(f"\n{len(tests)} passed")
