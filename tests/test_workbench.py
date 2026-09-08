"""Workbench selections retain source context and compose through the real SDK."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openmodels import Catalog, Manifest
from openmodels.contracts import dumps, loads
from test_universal import fixture


class WorkbenchTests(unittest.TestCase):
  def test_contextual_index_exports_through_sdk_and_cli(self):
    from web.directory import component_index
    data, original, _ = fixture()
    changed = original.data
    changed["members"]["head"]["source"]["revision"] = "two"
    changed["members"]["head"]["configuration"] = {}
    changed["members"]["head"]["missing"] = ["scale"]
    changed["configuration"] = {}
    second = Manifest.create(changed)
    data["documents"][second.id] = second.raw
    data["entries"].append({**deepcopy(data["entries"][0]), "recipe": second.id, "name": "Second context"})
    catalog = Catalog(dumps(data))
    index = component_index(catalog)
    self.assertEqual(index["revision"], catalog.revision)
    self.assertEqual(len(index["components"]), 4)
    self.assertEqual({p["id"] for p in index["profiles"]}, {original.profile.id})
    self.assertEqual(index["profiles"][0]["required_configuration"], ["scale"])
    head = next(c for c in index["components"] if c["recipe"] == second.id and c["role"] == "head")
    self.assertEqual(head["context"], "two")
    self.assertEqual(head["name"], "Second context")
    self.assertEqual(head["sha256"], original.data["members"]["head"]["artifact"]["sha256"])
    self.assertNotIn("members", head)
    self.assertNotIn("documents", index)
    request = {"profile": original.profile.id, "selection": {
      "encoder": {"recipe": original.id, "slot": "encoder"},
      "head": {"recipe": head["recipe"], "slot": head["role"]}}}
    unresolved, report = catalog.compose(**request)
    self.assertNotIn("scale", unresolved.data["configuration"])
    self.assertIn("configuration_unresolved", {finding["code"] for finding in report["findings"]})
    request["configuration"] = {"scale": 3}
    composed, report = catalog.compose(**request)
    self.assertEqual(composed.data["configuration"], {"scale": 3})
    self.assertEqual(composed.data["members"]["head"], changed["members"]["head"])
    self.assertEqual(composed.data["members"]["encoder"], original.data["members"]["encoder"])
    self.assertFalse(report["composition_attested"])
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      snapshot, selection = root / "catalog.json", root / "selection.json"
      snapshot.write_text(catalog.raw)
      selection.write_text(dumps(request))
      cli = subprocess.run([sys.executable, "-m", "openmodels", "--catalog", str(snapshot),
        "--sha256", catalog.revision, "compose", str(selection)], capture_output=True, text=True, check=True)
      exported = Catalog(cli.stdout)
      self.assertEqual(exported.resolve(composed.id), composed)
      self.assertEqual(loads(cli.stderr), report)

  def test_homepage_defers_component_details(self):
    from web.directory import render
    data, recipe, _ = fixture()
    data["entries"] *= 1000
    catalog = Catalog(dumps(data))
    page = render(catalog, lambda title, body: body, component_url="components-test.json")
    self.assertIn("components-test.json", page)
    self.assertIn(catalog.revision, page)
    self.assertIn("composition request", page.lower())
    self.assertIn("<noscript>", page)
    self.assertNotIn("data-directory-entry", page)
    self.assertNotIn(recipe.manifest.raw, page)
    self.assertNotIn("/v1/compose", page)
    self.assertLess(len(page), 70000)


if __name__ == "__main__":
  unittest.main()
