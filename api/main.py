"""Universal directory endpoints; HTTP and offline composition use the same SDK."""
from functools import lru_cache
from pathlib import Path

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from openmodels import Catalog, ContractError
from openmodels.contracts import SCHEMAS, dumps, loads, schema, validate

DATA_DIR = Path(os.environ.get("OPENMODELS_DATA", "data"))
app = FastAPI(title="OpenModels", version="0.1.0")
app.add_middleware(CORSMiddleware,
  allow_origins=[origin.strip() for origin in os.environ.get("OPENMODELS_CORS_ORIGINS", "").split(",") if origin.strip()],
  allow_methods=["GET", "POST"], allow_headers=["Content-Type", "If-Match"], expose_headers=["ETag"])


@lru_cache(maxsize=2)
def _read_catalog(path, mtime, size):
  return Catalog.load(path)


def catalog():
  path = Path(DATA_DIR) / "public" / "catalog.json"
  try:
    stat = path.stat()
    return _read_catalog(str(path), stat.st_mtime_ns, stat.st_size)
  except FileNotFoundError as exc:
    raise HTTPException(503, "Universal catalog not published; run python -m index.registry") from exc


@app.get("/v1/catalog", summary="Complete offline-capable snapshot")
def snapshot():
  cat = catalog()
  return Response(cat.raw, media_type="application/json", headers={"ETag": f'"{cat.revision}"'})


@app.get("/v1/models")
def models(query: str = "", publisher: str | None = None, kind: str | None = None,
           include_archive: bool = False, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
  cat = catalog()
  entries = cat.models(query=query, publisher=publisher, kind=kind, include_archive=include_archive)
  return {"revision": cat.revision, "total": len(entries), "offset": offset, "models": entries[offset:offset + limit]}


@app.get("/v1/models/{identity:path}")
def model(identity: str):
  try:
    return catalog().model(identity)
  except ContractError as exc:
    raise HTTPException(404, str(exc)) from exc


@app.get("/v1/manifests/{digest}")
def manifest(digest: str):
  try:
    document = catalog().manifest(digest)
  except ContractError as exc:
    raise HTTPException(404, str(exc)) from exc
  return Response(document.raw, media_type="application/json",
                  headers={"ETag": f'"{document.id}"', "Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/v1/profiles")
def profiles():
  return [{"id": m.id, **m.data} for m in catalog()._documents.values() if m.data["type"] == "profile"]


@app.get("/v1/artifacts/{digest}")
def locations(digest: str):
  location = catalog()._data["locations"].get(digest)
  if location is None:
    raise HTTPException(404, "Unknown artifact")
  return location


@app.get("/v1/schemas/{name}")
def schemas(name: str):
  if name not in SCHEMAS:
    raise HTTPException(404, "Unknown schema")
  return schema(name)


@app.get("/v1/status")
def status():
  cat = catalog()
  return {"schema": 1, "revision": cat.revision, "generated_at": cat._data["generated_at"],
          "sources": cat._data["sources"], "recipes": len(cat._data["entries"])}


@app.post("/v1/compose", openapi_extra={"requestBody": {"required": True, "content": {
  "application/json": {"schema": schema("compose")}}}})
async def compose(request: Request):
  raw = bytearray()
  async for chunk in request.stream():
    raw.extend(chunk)
    if len(raw) > 64 * 1024:
      raise HTTPException(413, "Composition request too large")
  try:
    body = loads(bytes(raw), 64 * 1024)
    validate(body, SCHEMAS["compose"])
    cat = catalog()
    if expected := request.headers.get("if-match"):
      if expected != f'"{cat.revision}"':
        raise HTTPException(412, "The directory and API use different catalog revisions. Refresh the page and try again.")
    recipe, report = cat.compose(**body)
    return {"id": recipe.id, "recipe": recipe.data, "report": report,
            "snapshot": loads(cat.export(recipe), 64 * 1024 * 1024)}
  except ContractError as exc:
    raise HTTPException(422, str(exc)) from exc
