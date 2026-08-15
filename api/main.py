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
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query

from index import code as codes
from index import compose
from fastapi.responses import RedirectResponse

DATA_DIR = Path(os.environ.get("OPENMODELS_DATA", "data"))
INDEX_PATH = DATA_DIR / "index.json"
BLOB_BACKEND = os.environ.get("BLOB_BACKEND", "github")
# Fallback only; the index records the repo it was published to.
RELEASE_REPO = os.environ.get("RELEASE_REPO", "jjolano/openmodels")
RELOAD_SECONDS = int(os.environ.get("RELOAD_SECONDS", "60"))
LOCAL_BLOB_DIR = Path(os.environ.get("LOCAL_BLOB_DIR", DATA_DIR / "public" / "blobs"))
OID_RE = re.compile(r"[0-9a-f]{64}")
RELEASE_RE = re.compile(r"blobs-[0-9]{4}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

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


def bundle_statuses(bundle: dict[str, Any]) -> list[str]:
  """Occurrence statuses without inventing one status for a reused file set."""
  return sorted({o["status"] for o in bundle["occurrences"]})


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
    "mirrored_count": index.get("mirrored_count", 0),
    "mirror_unavailable_count": len(index.get("mirror_unavailable", [])),
    "mirror_failure_count": len(index.get("mirror_failures", [])),
    "metadata_count": sum("metadata" in f for f in index["files"]),
    "attested_pairing_count": len(index.get("attested_pairings", [])),
    "archived_ref_count": sum(not b.get("upstream_reachable", True)
                              for b in index["bundles"]),
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
  limit: int = Query(200, ge=1, le=1000),
  offset: int = Query(0, ge=0),
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
    bundles = [b for b in bundles if status in bundle_statuses(b)]
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
        "statuses": bundle_statuses(b),
        "in_head": b["in_head"],
        "upstream_reachable": b.get("upstream_reachable", True),
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
  shipped = [o for o in bundle["occurrences"]
             if o["status"] in ("merged", "reverted")]
  index = load_index()
  pairings = index.get("attested_pairings", [])
  files_by_oid = {f["oid"]: f for f in index["files"]}
  lineage, partners = {}, {}
  for member in bundle["files"]:
    record = files_by_oid.get(member["oid"], {})
    entry = (record.get("metadata") or {}).get("lineage")
    if entry:
      ckpt = entry.get("self") or entry.get("vision")
      lineage[member["role"]] = entry
      if ckpt:
        partners[member["role"]] = compose.partners_of(ckpt, pairings)

  return {
    "bundle_id": bundle["bundle_id"],
    "source": "upstream",
    "attested": bool(shipped),
    "ran_in": (f"commaai/openpilot@{min(shipped, key=lambda o: o['date'])['commit']}"
               if shipped else None),
    "lineage": lineage,
    "attested_partners": partners,
    "introduced_by": introduced,
    "host_constants": bundle.get("host_constants", {}),
    "host_constants_sources": bundle.get("host_constants_sources", {}),
    "host_constants_missing": bundle.get("host_constants_missing", []),
    "host_contexts": bundle.get("host_contexts", []),
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


def _composed(selection: dict[str, str], index: dict[str, Any],
              code: str | None = None) -> dict[str, Any]:
  """Compose a selection and mint its code, turning every refusal into a 422.

  Shared by both endpoints on purpose: a code asserts nothing, so redeeming one must run the
  identical check set a fresh compose does.
  """
  files_by_oid = {f["oid"]: f for f in index["files"]}
  try:
    manifest = compose.compose(selection, files_by_oid, index.get("attested_pairings", []))
    return {**manifest, "code": code or codes.encode(selection)}
  except (compose.ComposeError, codes.CodeError) as exc:
    raise HTTPException(422, str(exc)) from exc


@app.post("/v1/compose")
def compose_bundle(selection: dict[str, str]) -> dict[str, Any]:
  """Assemble a bundle from indexed halves — `{"vision": oid, "on_policy": oid}`.

  Stateless: `bundle_id` is derived from the members, so nothing is stored and the manifest
  returned is the whole artifact. Pass it to the runtime library exactly like a provenance
  record.

  This is how upstream already works — comma ships one vision encoder against several policies.
  What the response adds is whether *this* pairing is one that shipped. It never has been driven
  in this exact combination, which is why `attested` is always false.
  """
  return _composed(selection, load_index())


@app.get("/v1/compose/{code}")
def resolve_code(code: str) -> dict[str, Any]:
  """Redeem a shareable code into a full composed manifest.

  The code carries truncated oids, so this resolves them against the catalog and re-runs every
  check. Redemption is where validation happens — the code itself asserts nothing, which is why
  a damaged one fails here instead of quietly naming different weights.
  """
  index = load_index()
  try:
    selection = codes.resolve(code, [f["oid"] for f in index["files"]])
  except codes.CodeError as exc:
    raise HTTPException(422, str(exc)) from exc
  return _composed(selection, index, code=code)


@app.get("/v1/lineage/{checkpoint:path}")
def get_lineage(checkpoint: str) -> dict[str, Any]:
  """What a training checkpoint has shipped alongside upstream.

  The practical question when combining halves: given this vision encoder, which policies were
  built against it? Answered from provenance, never inferred.
  """
  index = load_index()
  pairings = index.get("attested_pairings", [])
  partners = compose.partners_of(checkpoint, pairings)
  if not partners["as_vision"] and not partners["as_policy"]:
    raise HTTPException(404, f"no attested pairings for checkpoint {checkpoint}")
  return {"checkpoint": checkpoint, **partners}


@app.get("/v1/files/{oid}")
def get_file(oid: str) -> dict[str, Any]:
  record = _find_file(oid, load_index())
  return {**record, "download": f"/v1/files/{oid}/download"}


def _find_file(oid: str, index: dict[str, Any]) -> dict[str, Any]:
  if not OID_RE.fullmatch(oid):
    raise HTTPException(404, f"no file {oid}")
  for record in index["files"]:
    if record["oid"] == oid:
      return record
  raise HTTPException(404, f"no file {oid}")


@app.get("/v1/files/{oid}/download")
def download_file(oid: str) -> RedirectResponse:
  """Always a redirect, never a stream.

  Keeping blobs out of the Python path means a dead API costs live queries only — the static
  tree and the release assets keep serving. It also avoids uvicorn shipping a 296MB file.
  """
  index = load_index()
  record = _find_file(oid, index)

  if BLOB_BACKEND == "local":
    if not (LOCAL_BLOB_DIR / f"{oid}.onnx").is_file():
      raise HTTPException(503, f"file {oid} is indexed but not present in the local mirror")
    return RedirectResponse(f"/blobs/{oid}.onnx", status_code=302)

  # Blobs are sharded across releases (1000 assets each), so the shard is data, not a formula.
  # Without one the blob simply isn't mirrored yet — say so rather than 302 to a 404.
  release = record.get("release")
  if not release:
    if oid in index.get("mirror_unavailable", []):
      raise HTTPException(410, f"file {oid} is no longer available from the upstream LFS store")
    raise HTTPException(
      503,
      f"file {oid} is indexed but not yet mirrored; retry once the publisher has run",
    )
  repo = index.get("release_repo") or RELEASE_REPO
  if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
    raise HTTPException(503, "catalog has an invalid release repository")
  if not isinstance(release, str) or not RELEASE_RE.fullmatch(release):
    raise HTTPException(503, f"file {oid} has an invalid recorded release")
  return RedirectResponse(
    f"https://github.com/{quote(repo, safe='/')}/releases/download/"
    f"{quote(release, safe='')}/{oid}.onnx", status_code=302
  )
