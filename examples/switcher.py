"""Minimal consumer: python examples/switcher.py --demo (install the SDK first)."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread

from openmodels import Catalog, Manifest
from openmodels.contracts import dumps, sha256
from openmodels.selection import ModelSwitcher


def demo():
  """List and install harmless fixture bytes through the real HTTP downloader."""
  with tempfile.TemporaryDirectory() as root:
    root = Path(root)
    payload = b"OpenModels switcher demonstration; not a driving model."
    digest = sha256(payload)
    (root / digest).write_bytes(payload)
    profile = Manifest.create({"schema": 1, "type": "profile", "name": "example/demo/v1",
      "slot_sets": [["model"]], "connections": [], "required_configuration": [],
      "inputs": {}, "outputs": {}, "state": {}, "implementation_source": {"publisher": "example"}})
    recipe = Manifest.create({"schema": 1, "type": "recipe", "profile": profile.id,
      "configuration": {}, "members": {"model": {
        "artifact": {"sha256": digest, "size": len(payload), "format": "fixture"},
        "source": {"publisher": "example"}, "configuration": {}, "missing": [],
        "inputs": {}, "outputs": {}, "targets": ["CPU"], "metadata": {}}}})
    data = {"schema": 1, "generated_at": "2026-09-07T00:00:00Z", "sources": {},
      "documents": {profile.id: profile.raw, recipe.id: recipe.raw},
      "entries": [{"name": "Example model", "publisher": "example", "kind": "demonstration",
                   "recipe": recipe.id, "occurrences": []}],
      "locations": {digest: {"availability": "available", "urls": ["/" + digest]}}, "evidence": []}
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
      catalog = Catalog(dumps(data), base_url=f"http://127.0.0.1:{server.server_port}/")
      switcher = ModelSwitcher(catalog, root / "store", support=lambda r: None if r.id == recipe.id else "Not admitted")
      choices = switcher.list_models()
      assert choices and choices[0]["name"] == "Example model"
      print(choices[0]["name"])
      package = switcher.install(choices[0]["variants"][0]["recipe"],
          on_progress=lambda sha, received, total: print(f"Downloaded {received}/{total} bytes"))
      package.verify()
      assert package.artifact("model").read_bytes() == payload
      assert switcher.list_models()[0]["variants"][0]["installed"]
      print("Installed and verified. Activation belongs to the consumer.")
    finally:
      server.shutdown()
      server.server_close()
      thread.join()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--demo", action="store_true")
  parser.add_argument("--catalog")
  parser.add_argument("--policy", help="JSON object containing allowed_recipe_ids")
  parser.add_argument("--store", default="./models")
  parser.add_argument("command", nargs="?", choices=("list", "install"), default="list")
  parser.add_argument("recipe", nargs="?")
  args = parser.parse_args()
  if args.demo:
    demo()
    return
  if not args.catalog or not args.policy:
    parser.error("--catalog and --policy are required outside --demo")
  policy = json.loads(Path(args.policy).read_text())
  allowed = policy.get("allowed_recipe_ids") if isinstance(policy, dict) else None
  if not isinstance(allowed, list) or not all(isinstance(v, str) and len(v) == 64 and
      all(c in "0123456789abcdef" for c in v) for v in allowed):
    parser.error("policy needs an allowed_recipe_ids array of full SHA-256 recipe digests")
  switcher = ModelSwitcher(Catalog.load(args.catalog), args.store,
      support=lambda recipe: None if recipe.id in allowed else "Not admitted by this consumer")
  if args.command == "list":
    print(json.dumps(switcher.list_models(), indent=2))
  else:
    if not args.recipe:
      parser.error("install requires an exact recipe digest")
    package = switcher.install(args.recipe,
        on_progress=lambda sha, received, total: print(f"{sha[:12]}: {received}/{total}"))
    print(package.path)


if __name__ == "__main__":
  main()
