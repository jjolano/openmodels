"""Build identity, upload skipping, manifest determinism and rendered build blocks."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from ci.builds import build_id, load_builds, manifest, needs_compile, stock_recipe_id
from openmodels.contracts import dumps, loads, sha256
from openmodels.profiles import STOCK_SHA256
from test_catalog import fixture


TARGET = {"backend": "QCOM", "hardware": "comma3x", "os": "unvalidated",
          "runtime": "tinygrad@138fb4a783d82f4e877ad2fe3692aaf8d1de2e46",
          "options": {"camera_width": 1928, "camera_height": 1208, "deadline_ms": 50}}
SOURCE, IMPLEMENTATION, TOOLCHAIN = STOCK_SHA256, "b" * 64, "a9e8aa32" * 8


def record(recipe, identity, inputs):
  return {"schema": 1, "build_id": identity, "inputs": inputs, "recipe": recipe,
          "source": {"sha256": STOCK_SHA256, "size": 60881999}, "target": deepcopy(TARGET),
          "artifact": {"name": f"model-{identity}.pkl", "url": "https://example.com/model.pkl",
                       "sha256": "c" * 64, "size": 123},
          "compiler": {"implementation": IMPLEMENTATION, "toolchain_sha256": TOOLCHAIN,
                       "cross_compiler_sha256": "d" * 64, "host_target": "CPU:LLVM",
                       "python": "3.12.0", "programs": 77, "kernel_bytes": 651024},
          "checks": ["weighted-matmul-oracle", "policy-and-warp-seeded-replay", "pickle-round-trip",
                     "target-program-inspection"],
          "gpu_validated": False, "device_validated": False, "created_at": "2026-01-01T00:00:00Z"}


class PrecompileTests(unittest.TestCase):
  def test_build_identity_tracks_every_compiler_input(self):
    recipe = "e" * 64
    inputs, identity = build_id(recipe, SOURCE, IMPLEMENTATION, TOOLCHAIN, TARGET)
    bound = {"recipe": recipe, "source_sha256": SOURCE, "implementation": IMPLEMENTATION,
             "toolchain_sha256": TOOLCHAIN, "target": TARGET}
    self.assertEqual(inputs, sha256(dumps(bound).encode()))
    self.assertEqual(identity, inputs[:16])
    for altered in ((recipe + "a", SOURCE, IMPLEMENTATION, TOOLCHAIN, TARGET),
                    (recipe, "f" * 64, IMPLEMENTATION, TOOLCHAIN, TARGET),
                    (recipe, SOURCE, "0" * 64, TOOLCHAIN, TARGET),
                    (recipe, SOURCE, IMPLEMENTATION, "0" * 64, TARGET),
                    (recipe, SOURCE, IMPLEMENTATION, TOOLCHAIN, {**TARGET, "hardware": "comma4"})):
      self.assertNotEqual(build_id(*altered)[1], identity)

  def test_needs_compile_skips_an_existing_record(self):
    identity = build_id("e" * 64, SOURCE, IMPLEMENTATION, TOOLCHAIN, TARGET)[1]
    self.assertTrue(needs_compile([], identity))
    self.assertTrue(needs_compile(["model-abc.pkl", "builds.json"], identity))
    self.assertFalse(needs_compile([f"build-{identity}.json"], identity))

  def test_manifest_is_sorted_deterministic_and_round_trips(self):
    first, second = record("1" * 64, "b" * 16, "1" * 64), record("2" * 64, "a" * 16, "2" * 64)
    raw = manifest([first, second])
    # Identical records must serialize identically, or every run would re-upload the manifest.
    self.assertEqual(raw, manifest([second, first]))
    self.assertEqual([item["build_id"] for item in loads(raw)["builds"]], ["a" * 16, "b" * 16])
    self.assertEqual(loads(raw)["generated_at"], "2026-01-01T00:00:00Z")
    self.assertEqual(loads(manifest([]))["builds"], [])
    with tempfile.TemporaryDirectory() as root:
      path = Path(root) / "builds.json"
      path.write_text(raw)
      self.assertEqual(load_builds(path), loads(raw)["builds"])
    self.assertEqual(load_builds(None), [])

  def test_pinned_recipe_id_is_the_document_digest(self):
    pinned = loads((Path(__file__).parents[1] / "index/stock-supercombo.json").read_bytes())
    identity = stock_recipe_id()
    self.assertEqual(sha256(pinned["documents"][identity].encode()), identity)

  def test_model_page_shows_only_recorded_builds(self):
    from openmodels import Catalog
    from web.models import detail
    data, recipe, _ = fixture()
    data["entries"][0]["model"] = {"id": "example/model", "name": "Named model", "family": "segmentation",
                                   "description": "Example.", "links": [], "archived": False}
    catalog = Catalog(dumps(data))
    model = catalog.model("example/model")
    bound = record(recipe.id, "a" * 16, "1" * 64)
    page = detail(catalog, model, lambda title, body: body, builds=[bound])
    self.assertIn("Precompiled a630 build (off-device)", page)
    self.assertIn(bound["artifact"]["sha256"], page)
    self.assertIn(bound["artifact"]["url"], page)
    self.assertIn("QCOM / comma3x", page)
    self.assertIn("weighted matmul oracle", page)
    self.assertIn("no device validation is claimed", page)
    page = detail(catalog, model, lambda title, body: body, builds=[record("9" * 64, "b" * 16, "2" * 64)])
    self.assertNotIn("Precompiled a630 build", page)

  def test_site_rejects_a_build_for_an_unknown_recipe(self):
    from ci.site import build
    from openmodels import ContractError
    from test_publication import source
    with tempfile.TemporaryDirectory() as root:
      root = Path(root)
      index = root / "index.json"
      index.write_text(dumps(source()))
      with self.assertRaises(ContractError):
        build(index, root / "public", "code", "archive", [record("9" * 64, "a" * 16, "1" * 64)])
      built = build(index, root / "public", "code", "archive", [])
      self.assertGreater(built.search()["total"], 0)


if __name__ == "__main__":
  unittest.main()
