"""Convert the comma archive and reviewed publisher snapshots to the universal directory."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

from index.lineage import seam_width
from openmodels import Catalog, ContractError, Manifest
from openmodels.contracts import SCHEMAS, dumps, loads, schema


def archive_profile(bundle):
  roles = sorted(f["role"] for f in bundle["files"])
  # Archive profiles describe structural slots; no execution semantics are inferred.
  return Manifest.create({"schema": 1, "type": "profile",
    "name": f"comma/archive/{bundle['kind']}/{'-'.join(roles)}/v1",
    "slot_sets": [roles],
    "connections": [{"from": "vision", "output": "features", "to": role, "input": "features"}
                    for role in roles if "vision" in roles and role in ("on_policy", "off_policy")],
    "required_configuration": ["frame_skip", "LAT_SMOOTH_SECONDS", "LONG_SMOOTH_SECONDS"] if bundle["kind"] == "driving" else [],
    "inputs": {"status": "source metadata only"}, "outputs": {"status": "source metadata only"},
    "state": {"status": "unresolved; requires an explicit execution profile"},
    "implementation_source": {"repository": "commaai/openpilot", "status": "historical archive"}})


def convert(index, *, blob_base=None):
  files = {f["oid"]: f for f in index["files"]}
  result = {"schema": 1, "generated_at": index["generated_at"],
            "sources": {"commaai/openpilot": {"head": index["upstream_head"]}},
            "documents": {}, "entries": [], "locations": {}, "evidence": []}
  for oid, record in files.items():
    urls = []
    if blob_base and record.get("local_mirrored"):
      urls = [f"{blob_base.rstrip('/')}/{oid}.onnx"]
    elif record.get("release"):
      repo = index.get("release_repo", "jjolano/openmodels")
      urls = [f"https://github.com/{repo}/releases/download/{record['release']}/{oid}.onnx"]
    gone = oid in index.get("mirror_unavailable", [])
    result["locations"][oid] = {"urls": urls, "availability": "available" if urls else "gone" if gone else "pending"}
  for bundle in index["bundles"]:
    profile = archive_profile(bundle)
    result["documents"][profile.id] = profile.raw
    contexts = bundle.get("host_contexts") or [{
      "commit": bundle.get("introduced_by", {}).get("commit"),
      "host_constants": bundle.get("host_constants", {}),
      "host_constants_sources": bundle.get("host_constants_sources", {}),
      "host_constants_missing": bundle.get("host_constants_missing", []),
    }]
    for context in contexts:
      configuration = dict(context["host_constants"])
      run, trained = configuration.get("MODEL_RUN_FREQ"), configuration.get("MODEL_CONTEXT_FREQ")
      if run and trained and run % trained == 0:
        configuration["frame_skip"] = int(run // trained)
      members = {}
      for file in bundle["files"]:
        record = files[file["oid"]]
        meta = record.get("metadata") or {}
        inputs, outputs = {}, {}
        width = seam_width(meta)
        if width:
          outputs["features"] = {"shape": [width], "dtype": None, "semantics": None}
        features = meta.get("input_shapes", {}).get("features_buffer")
        if features and len(features) == 3 and features[-1] > 0:
          inputs["features"] = {"shape": [features[-1]], "dtype": None, "semantics": None}
        members[file["role"]] = {
          "artifact": {"sha256": file["oid"], "size": file["size"], "format": "onnx"},
          "source": {"repository": "commaai/openpilot", "commit": context.get("commit"),
                     "path": file.get("path", file["filename"]), "license": "MIT",
                     "constants_sources": context.get("host_constants_sources", {})},
          "configuration": configuration, "missing": context.get("host_constants_missing", []),
          "inputs": inputs, "outputs": outputs,
          "targets": ["AMD" if bundle["variant"] == "big" else "QCOM"], "metadata": meta}
      recipe = Manifest.create({"schema": 1, "type": "recipe", "profile": profile.id,
                                "members": members, "configuration": configuration})
      result["documents"][recipe.id] = recipe.raw
      result["entries"].append({"name": bundle["name"], "publisher": "commaai", "kind": bundle["kind"],
                                "recipe": recipe.id, "occurrences": bundle["occurrences"]})
    artifacts = sorted(f["oid"] for f in bundle["files"])
    if len(artifacts) > 1 and any(o["status"] in ("merged", "reverted") for o in bundle["occurrences"]):
      result["evidence"].append({"kind": "upstream_pairing", "source": {
        "repository": "commaai/openpilot", "bundle": bundle["bundle_id"], "occurrences": bundle["occurrences"]},
        "artifacts": artifacts})
  return result


def merge_publishers(snapshot, directory):
  for path in sorted(Path(directory).glob("*/*.json")):
    submission = Catalog.load(path).data
    if any(e["publisher"] != path.parent.name for e in submission["entries"]):
      raise ContractError(f"publisher namespace does not match directory: {path}")
    for key in ("documents", "locations", "sources"):
      for identity, value in submission[key].items():
        if identity in snapshot[key] and snapshot[key][identity] != value:
          raise ContractError(f"publisher submission conflicts with existing {key}: {identity}")
        snapshot[key][identity] = value
    snapshot["entries"].extend(submission["entries"])
    snapshot["evidence"].extend(submission["evidence"])
  return snapshot


def atomic_write(path, raw):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
    staged = Path(handle.name)
    handle.write(raw.encode() if isinstance(raw, str) else raw)
  try:
    os.replace(staged, path)
  finally:
    staged.unlink(missing_ok=True)


def publish(index, out, *, publishers=None, blob_base=None):
  snapshot = convert(index, blob_base=blob_base)
  if publishers:
    merge_publishers(snapshot, publishers)
  raw = dumps(snapshot)
  catalog = Catalog(raw)  # Validate the complete graph before publishing any discovery file.
  out = Path(out)
  for digest, document in snapshot["documents"].items():
    atomic_write(out / "manifests" / f"{digest}.json", document)
  for name in SCHEMAS:
    atomic_write(out / "schemas" / f"{name}.json", dumps(schema(name)))
  atomic_write(out / "snapshots" / f"{catalog.revision}.json", raw)
  atomic_write(out / "catalog.json", raw)
  return catalog


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--index", default="data/index.json")
  parser.add_argument("--out", default="data/public")
  parser.add_argument("--publishers", default="publishers")
  parser.add_argument("--blob-base")
  args = parser.parse_args()
  index = loads(Path(args.index).read_bytes(), 64 * 1024 * 1024)
  catalog = publish(index, args.out, publishers=args.publishers, blob_base=args.blob_base)
  print(f"catalog {catalog.revision}: {catalog.search()['total']} recipes")


if __name__ == "__main__":
  main()
