"""openmodels API — identity and provenance for openpilot driving models.

This service reports what a model *is* and where it *came from*. It renders no compatibility
verdict: structural similarity is not interchangeability (see AGENTS.md), so there is
deliberately no /v1/compat endpoint.

The catalog is a few hundred bundles and loads into memory. There is no database because there
is nothing here a list comprehension cannot answer.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

DATA_DIR = Path(os.environ.get("OPENMODELS_DATA", "data"))
INDEX_PATH = DATA_DIR / "index.json"
BLOB_BACKEND = os.environ.get("BLOB_BACKEND", "github")
# Fallback only; the index records the repo it was published to.
RELEASE_REPO = os.environ.get("RELEASE_REPO", "OWNER/openmodels")
RELOAD_SECONDS = int(os.environ.get("RELOAD_SECONDS", "60"))

DISCLAIMER = (
  "Provenance only. This registry asserts blob identity and upstream history. It does not "
  "assert that a model is safe, or that two models are interchangeable. Structural similarity "
  "does not imply compatible semantics."
)

app = FastAPI(
  title="openmodels",
  description=DISCLAIMER,
  version="1.0.0",
)

_cache: dict[str, Any] = {"index": None, "loaded_at": 0.0, "mtime": 0.0}


def load_index() -> dict[str, Any]:
  """Re-read index.json when it changes on disk. The indexer writes it out of band."""
  now = time.monotonic()
  if _cache["index"] is not None and now - _cache["loaded_at"] < RELOAD_SECONDS:
    return _cache["index"]
  if not INDEX_PATH.exists():
    raise HTTPException(503, f"index not built yet ({INDEX_PATH})")

  mtime = INDEX_PATH.stat().st_mtime
  if _cache["index"] is None or mtime != _cache["mtime"]:
    _cache["index"] = json.loads(INDEX_PATH.read_text())
    _cache["mtime"] = mtime
  _cache["loaded_at"] = now
  return _cache["index"]


def bundle_status(bundle: dict[str, Any]) -> str:
  """Worst-case-last status: what happened most recently to this file set."""
  latest = max(bundle["occurrences"], key=lambda o: o["date"])
  return latest["status"]


@app.get("/v1/status")
def status() -> dict[str, Any]:
  index = load_index()
  return {
    "generated_at": index["generated_at"],
    "upstream_head": index["upstream_head"],
    "bundle_count": index["bundle_count"],
    "file_count": index["file_count"],
    "schema": index["schema"],
    "blob_backend": BLOB_BACKEND,
    "disclaimer": DISCLAIMER,
  }


@app.get("/v1/models")
def list_models(
  kind: str | None = Query(None, description="driving | dmonitoring | nav"),
  family: str | None = Query(None, description="supercombo | split"),
  variant: str | None = Query(None, description="standard | big"),
  role: str | None = Query(None, description="only bundles containing this role"),
  status: str | None = Query(None, description="merged | reverted | pr_only"),
  in_head: bool | None = Query(None, description="present in upstream HEAD"),
  since: str | None = Query(None, description="ISO date; introduced on or after"),
  limit: int = Query(200, le=1000),
  offset: int = 0,
) -> dict[str, Any]:
  bundles = load_index()["bundles"]

  if kind:
    bundles = [b for b in bundles if b["kind"] == kind]
  if family:
    bundles = [b for b in bundles if b["family"] == family]
  if variant:
    bundles = [b for b in bundles if b["variant"] == variant]
  if role:
    bundles = [b for b in bundles if any(f["role"] == role for f in b["files"])]
  if status:
    bundles = [b for b in bundles if bundle_status(b) == status]
  if in_head is not None:
    bundles = [b for b in bundles if b["in_head"] == in_head]
  if since:
    bundles = [b for b in bundles if b["introduced_by"]["date"] >= since]

  total = len(bundles)
  page = bundles[offset:offset + limit]
  index = load_index()
  return {
    "total": total,
    "offset": offset,
    "count": len(page),
    # Freshness travels with every listing: a client that only ever calls /v1/models must still
    # be able to notice the indexer has stopped, which otherwise looks like a quiet upstream.
    "generated_at": index["generated_at"],
    "upstream_head": index["upstream_head"],
    "disclaimer": DISCLAIMER,
    "bundles": [
      {
        "bundle_id": b["bundle_id"],
        "name": b["name"],
        "slug": b["slug"],
        "kind": b["kind"],
        "family": b["family"],
        "variant": b["variant"],
        "status": bundle_status(b),
        "in_head": b["in_head"],
        "introduced_by": b["introduced_by"],
        "roles": [f["role"] for f in b["files"]],
        "total_bytes": sum(f["size"] for f in b["files"]),
      }
      for b in page
    ],
  }


def _find(bundle_id: str) -> dict[str, Any]:
  for bundle in load_index()["bundles"]:
    if bundle["bundle_id"] == bundle_id:
      return bundle
  raise HTTPException(404, f"no bundle {bundle_id}")


@app.get("/v1/models/{bundle_id}")
def get_model(bundle_id: str) -> dict[str, Any]:
  return {**_find(bundle_id), "disclaimer": DISCLAIMER}


@app.get("/v1/models/{bundle_id}/provenance")
def get_provenance(bundle_id: str) -> dict[str, Any]:
  """The compatibility surface: verifiable facts, not a verdict.

  Every field here can be independently re-checked against commaai/openpilot. A fork compares
  its own configuration to `host_constants` and decides for itself.
  """
  bundle = _find(bundle_id)
  introduced = bundle["introduced_by"]
  return {
    "bundle_id": bundle["bundle_id"],
    "ran_in": f"commaai/openpilot@{introduced['commit']}",
    "introduced_by": introduced,
    "host_constants": bundle.get("host_constants", {}),
    "host_constants_sources": bundle.get("host_constants_sources", {}),
    "host_constants_missing": bundle.get("host_constants_missing", []),
    "frame_skip": bundle.get("frame_skip"),
    "files": [
      {"role": f["role"], "filename": f["filename"], "path": f["path"],
       "oid": f["oid"], "size": f["size"]}
      for f in bundle["files"]
    ],
    "occurrences": bundle["occurrences"],
    "in_head": bundle["in_head"],
    "verify": "sha256 of each downloaded file MUST equal its oid; refuse the blob otherwise",
    "disclaimer": DISCLAIMER,
  }


@app.get("/v1/files/{oid}")
def get_file(oid: str) -> dict[str, Any]:
  for record in load_index()["files"]:
    if record["oid"] == oid:
      return {**record, "download": f"/v1/files/{oid}/download"}
  raise HTTPException(404, f"no file {oid}")


@app.get("/v1/files/{oid}/download")
def download_file(oid: str) -> RedirectResponse:
  """Always a redirect, never a stream.

  Keeping blobs out of the Python path means a dead API costs live queries only — the static
  tree and the release assets keep serving. It also avoids uvicorn shipping a 296MB file.
  """
  index = load_index()
  record = next((r for r in index["files"] if r["oid"] == oid), None)
  if record is None:
    raise HTTPException(404, f"no file {oid}")

  if BLOB_BACKEND == "local":
    return RedirectResponse(f"/blobs/{oid}.onnx", status_code=302)

  # Blobs are sharded across releases (1000 assets each), so the shard is data, not a formula.
  # Without one the blob simply isn't mirrored yet — say so rather than 302 to a 404.
  release = record.get("release")
  if not release:
    raise HTTPException(
      503,
      f"file {oid} is indexed but not yet mirrored; retry once the publisher has run",
    )
  repo = index.get("release_repo") or RELEASE_REPO
  return RedirectResponse(
    f"https://github.com/{repo}/releases/download/{release}/{oid}.onnx", status_code=302
  )
