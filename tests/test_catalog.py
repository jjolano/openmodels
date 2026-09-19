"""Runnable contract and transport checks: python -m unittest discover -s tests."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from openmodels import Catalog, ContractError, Manifest, ModelStore, Recipe
from openmodels.contracts import SCHEMAS, dumps, loads, schema, sha256


def fixture():
  profile = Manifest.create({"schema": 1, "type": "profile", "name": "example/segmentation/v1",
    "slot_sets": [["encoder", "head"]], "connections": [{"from": "encoder", "output": "latent", "to": "head", "input": "latent"}],
    "required_configuration": ["scale"], "inputs": {"image": "RGB"}, "outputs": {"mask": "class IDs"},
    "state": {"mode": "stateless"}, "implementation_source": {"publisher": "example"}})
  members, payloads = {}, {}
  for role in ("encoder", "head"):
    raw = f"fixture weights: {role}".encode()
    digest = sha256(raw)
    payloads[digest] = raw
    port = {"shape": [4], "dtype": "float32", "semantics": "example/latent-a/v1"}
    members[role] = {"artifact": {"sha256": digest, "size": len(raw), "format": "fixture"},
      "source": {"publisher": "example", "revision": "one", "license": "CC0-1.0"},
      "configuration": {"scale": 1}, "missing": [], "inputs": {"latent": port} if role == "head" else {},
      "outputs": {"latent": port} if role == "encoder" else {}, "targets": ["CPU"], "metadata": {}}
  recipe = Recipe(Manifest.create({"schema": 1, "type": "recipe", "profile": profile.id,
                                   "members": members, "configuration": {"scale": 1}}), profile)
  data = {"schema": 1, "generated_at": "2026-09-07T00:00:00Z", "sources": {"example": {"revision": "one"}},
    "documents": {profile.id: profile.raw, recipe.id: recipe.manifest.raw},
    "entries": [{"name": "Example segmentation", "publisher": "example", "kind": "segmentation", "recipe": recipe.id,
                 "occurrences": []}],
    "locations": {k: {"urls": [f"/{k}"], "availability": "available"} for k in payloads}, "evidence": []}
  return data, recipe, payloads


@contextmanager
def server(payloads):
  class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
      data = payloads.get(self.path)
      self.send_response(200 if data is not None else 404)
      self.end_headers()
      if data is not None:
        self.wfile.write(data)
    def log_message(self, *args):
      pass
  host = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
  thread = Thread(target=host.serve_forever, daemon=True)
  thread.start()
  try:
    yield f"http://127.0.0.1:{host.server_port}"
  finally:
    host.shutdown()
    host.server_close()
    thread.join()


class CatalogTests(unittest.TestCase):
  def test_identity_and_immutable_data(self):
    data, recipe, _ = fixture()
    for field in ("configuration", "artifact", "source"):
      changed = recipe.data
      if field == "configuration":
        changed[field]["scale"] = 2
      elif field == "artifact":
        changed["members"]["head"][field]["sha256"] = "a" * 64
      else:
        changed["members"]["head"][field]["revision"] = "two"
      self.assertNotEqual(Manifest.create(changed).id, recipe.id)
    profile = recipe.profile.data
    profile["state"]["mode"] = "recurrent"
    changed = recipe.data
    changed["profile"] = Manifest.create(profile).id
    self.assertNotEqual(Manifest.create(changed).id, recipe.id)
    self.assertEqual(recipe.data["configuration"]["scale"], 1)
    self.assertEqual(Manifest.create(dict(reversed(list(recipe.data.items())))).id, recipe.id)
    self.assertEqual(sha256(recipe.manifest.raw.encode()), recipe.id)

  def test_untrusted_data_and_unknown_profiles(self):
    for raw in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":1e999}', '[' * 60 + '0' + ']' * 60):
      with self.assertRaises(ContractError):
        loads(raw)
    data, recipe, _ = fixture()
    data["documents"][recipe.id] += " "
    with self.assertRaises(ContractError):
      Catalog(dumps(data))
    data, recipe, _ = fixture()
    self.assertEqual(Catalog(dumps(data)).search(kind="segmentation")["total"], 1)
    self.assertEqual(recipe.profile.data["name"], "example/segmentation/v1")
    invalid = recipe.data
    invalid["members"]["head"]["artifact"]["size"] = True
    with self.assertRaises(ContractError):
      Manifest.create(invalid)
    invalid = recipe.data
    invalid["members"]["head"]["artifact"] = {**invalid["members"]["encoder"]["artifact"], "size": 1}
    conflict = Manifest.create(invalid)
    data["documents"][conflict.id] = conflict.raw
    with self.assertRaisesRegex(ContractError, "conflicting descriptions"):
      Catalog(dumps(data))

  def test_export_and_pinned_recipe_merge(self):
    from index.registry import PINNED, merge_pinned
    data, recipe, _ = fixture()
    exported = loads(Catalog(dumps(data)).export(recipe))
    exported["entries"] = data["entries"]
    self.assertEqual(Catalog(dumps(exported)).resolve("Example segmentation"), recipe)
    empty = {**data, "entries": [], "documents": {}, "sources": {}, "locations": {}, "evidence": []}
    merged = Catalog(dumps(merge_pinned(empty)))
    pinned = Catalog.load(PINNED)
    stock = pinned.resolve("Stock supercombo (555f48c5)")
    self.assertEqual(merged.resolve("Stock supercombo (555f48c5)"), stock)
    self.assertLessEqual(set(pinned.data["documents"]), set(merged.data["documents"]))
    digest = next(iter(pinned.data["documents"]))
    conflicted = {**empty, "documents": {digest: pinned.data["documents"][digest] + " "}}
    with self.assertRaises(ContractError):
      merge_pinned(conflicted)

  def test_snapshots_pagination_and_downloads(self):
    data, recipe, blobs = fixture()
    data["entries"] *= 1001
    payloads = {"/catalog.json": dumps(data).encode(), **{f"/{k}": v for k, v in blobs.items()}}
    with server(payloads) as origin, tempfile.TemporaryDirectory() as root:
      cat = Catalog.load(origin + "/catalog.json", expected_sha256=sha256(payloads["/catalog.json"]))
      self.assertEqual(cat.search(offset=1000)["entries"], data["entries"][1000:])
      self.assertEqual(cat.search()["total"], 1001)
      store = ModelStore(Path(root) / "store", cat)
      installed = store.fetch(recipe)
      installed.verify()
      self.assertEqual(store.fetch(recipe), installed)
      altered = recipe.data
      altered["configuration"]["scale"] = 2
      sibling = Recipe(Manifest.create(altered), recipe.profile)
      with patch("openmodels.client.urlopen", side_effect=AssertionError("must reuse verified artifacts")):
        cached = store.fetch(sibling)
      self.assertEqual(installed.artifact("head").stat().st_ino, cached.artifact("head").stat().st_ino)
      self.assertFalse(hasattr(store, "set_active"))
      installed.artifact("head").write_bytes(b"corrupt")
      with self.assertRaises(ContractError):
        store.fetch(recipe)
      with self.assertRaises(ContractError):
        Catalog.load(origin + "/catalog.json", expected_sha256="0" * 64)

  def test_failed_download_never_installs(self):
    data, recipe, blobs = fixture()
    digest = recipe.data["members"]["head"]["artifact"]["sha256"]
    for bad in (b"truncated", b"x" * 1000):
      payloads = {f"/{k}": v for k, v in blobs.items()}
      payloads[f"/{digest}"] = bad
      with server(payloads) as origin, tempfile.TemporaryDirectory() as root:
        store = ModelStore(root, Catalog(dumps(data), base_url=origin))
        with self.assertRaises(ContractError):
          store.fetch(recipe)
        self.assertFalse((Path(root) / recipe.id).exists())
        self.assertEqual(list(Path(root).glob(".download-*")), [])

  def test_schemas_match_actual_documents(self):
    from jsonschema import Draft202012Validator
    data, recipe, _ = fixture()
    for name in SCHEMAS:
      Draft202012Validator.check_schema(schema(name))
    Draft202012Validator(schema("snapshot")).validate(data)
    Draft202012Validator(schema("recipe")).validate(recipe.data)
    Draft202012Validator(schema("profile")).validate(recipe.profile.data)

  def test_archive_configuration_is_not_collapsed(self):
    from index.registry import convert, publish
    oid = "a" * 64
    archive = {"schema": 1, "generated_at": "2026-09-07T00:00:00Z", "upstream_head": "commit",
      "files": [{"oid": oid, "size": 100, "release": "blobs-0004"}],
      "bundles": [{"bundle_id": "old", "kind": "driving", "variant": "standard", "name": "Stock",
        "files": [{"oid": oid, "size": 100, "filename": "supercombo.onnx", "role": "supercombo"}],
        "occurrences": [{"commit": "one", "status": "merged"}, {"commit": "two", "status": "reverted"}],
        "host_contexts": [{"commit": revision, "host_constants": {"LAT_SMOOTH_SECONDS": value}, "host_constants_missing": []}
                          for revision, value in (("one", 0), ("two", 0.1))]}]}
    cat = Catalog(dumps(convert(archive)))
    self.assertEqual(cat.search()["total"], 2)
    with self.assertRaisesRegex(ContractError, "resolves to 2 recipes"):
      cat.resolve("Stock")
    self.assertIn("blobs-0004", cat.data["locations"][oid]["urls"][0])
    with tempfile.TemporaryDirectory() as root:
      published = publish(archive, root)
      self.assertEqual(Catalog.load(Path(root) / "catalog.json").revision, published.revision)
      for digest, raw in published.data["documents"].items():
        self.assertEqual((Path(root) / "manifests" / f"{digest}.json").read_text(), raw)


  def test_publication_preserves_archive_and_escapes_names(self):
    from index.registry import convert
    from web.render import render
    oid = "a" * 64
    archive = {"schema": 1, "generated_at": "today", "upstream_head": "commit",
      "files": [{"oid": oid, "size": 100}],
      "bundles": [{"bundle_id": "archive-id", "kind": "driving", "variant": "standard",
        "name": "<script>alert(1)</script>", "occurrences": [],
        "files": [{"oid": oid, "size": 100, "filename": "supercombo.onnx", "role": "supercombo"}]}]}
    self.assertEqual(convert(archive, blob_base="/blobs")["locations"][oid],
                     {"availability": "pending", "urls": []})
    archive["files"][0]["local_mirrored"] = True
    self.assertEqual(convert(archive, blob_base="/blobs")["locations"][oid]["urls"], [f"/blobs/{oid}.onnx"])
    with tempfile.TemporaryDirectory() as root:
      source, output = Path(root) / "archive.json", Path(root) / "public"
      source.write_text(dumps(archive))
      render(source, output)
      self.assertEqual(loads(source.read_text()), archive)
      self.assertFalse((output / "index.json").exists())
      page = "".join(path.read_text() for path in output.glob("model-*.html"))
      self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
      self.assertNotIn("<script>alert(1)</script>", page)
      self.assertTrue({"index.html", "archive.html", "integrate.html"} <= {p.name for p in output.glob("*.html")})
      self.assertFalse((output / "compose.html").exists())
      self.assertFalse((output / "models.html").exists())
      cat = Catalog.load(output / "catalog.json")
      for entry in cat.data["entries"]:
        exported = Catalog.load(output / "recipes" / f"{entry['recipe']}.json")
        self.assertEqual(exported.resolve(entry["recipe"]), cat.resolve(entry["recipe"]))

  def test_archived_lineage_remains_provenance(self):
    from index.lineage import attested_pairings, seam_width
    files = {"v": {"metadata": {"lineage": {"self": "vision"}}},
             "p": {"metadata": {"lineage": {"self": "policy"}}}}
    bundle = {"files": [{"oid": "v", "role": "vision"}, {"oid": "p", "role": "on_policy"}],
              "occurrences": [{"status": "pr_only"}]}
    self.assertEqual(attested_pairings([bundle], files), [])
    bundle["occurrences"].append({"status": "reverted"})
    self.assertEqual(attested_pairings([bundle], files), [["vision", "policy"]])
    metadata = {"output_slices": {"hidden_state": [1064, -120, None]}}
    self.assertIsNone(seam_width(metadata))
    metadata["output_shapes"] = {"outputs": [1, 1696]}
    self.assertEqual(seam_width(metadata), 512)


if __name__ == "__main__":
  unittest.main()
