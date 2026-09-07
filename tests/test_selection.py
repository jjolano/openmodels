"""Consumer policy, installation state, cancellation and the executable example."""
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openmodels import Catalog, ContractError, Manifest, ModelStore
from openmodels.client import DownloadCancelled, Package
from openmodels.contracts import dumps
from openmodels.selection import ModelSwitcher
from test_universal import fixture


class SelectionTests(unittest.TestCase):
  def test_policy_before_io_and_state_preservation(self):
    data, recipe, blobs = fixture()
    changed = recipe.data
    changed["configuration"]["scale"] = 2
    other = Manifest.create(changed)
    data["documents"][other.id] = other.raw
    catalog = Catalog(dumps(data), base_url="http://example.test/")
    with tempfile.TemporaryDirectory() as root:
      switcher = ModelSwitcher(catalog, root, support=lambda r: None if r.id == recipe.id else "Consumer has not admitted this recipe")
      with patch("openmodels.client.urlopen", side_effect=lambda url, **kw: BytesIO(blobs[url.rsplit("/", 1)[-1]])):
        package = switcher.install(recipe.id)
      self.assertEqual(switcher.status["state"], "installed")
      self.assertTrue(switcher.list_models()[0]["variants"][0]["installed"])
      with patch("openmodels.client.urlopen", side_effect=AssertionError("policy must run first")):
        with self.assertRaisesRegex(ContractError, "not admitted"):
          switcher.install(other.id)
      self.assertEqual(switcher.package, package)
      self.assertEqual(switcher.status["state"], "failed")
      with self.assertRaises(DownloadCancelled):
        switcher.install(recipe.id, cancelled=lambda: True)
      self.assertEqual(switcher.status["state"], "cancelled")
      self.assertEqual(switcher.package, package)
      package.verify()

  def test_cancellation_never_retries_or_installs(self):
    data, recipe, blobs = fixture()
    for location in data["locations"].values():
      location["urls"] *= 2
    catalog = Catalog(dumps(data), base_url="http://example.test/")
    with tempfile.TemporaryDirectory() as root:
      cancel = False
      def progress(*args):
        nonlocal cancel
        cancel = True
      store = ModelStore(Path(root) / "store", catalog)
      with self.assertRaises(DownloadCancelled):
        store.fetch(recipe, cancelled=lambda: True)
      self.assertFalse(store.root.exists())
      with patch("openmodels.client.urlopen", side_effect=lambda url, **kw: BytesIO(blobs[url.rsplit("/", 1)[-1]])) as fetch:
        with self.assertRaises(DownloadCancelled):
          store.fetch(recipe, on_progress=progress, cancelled=lambda: cancel)
        self.assertEqual(fetch.call_count, 1)
      self.assertFalse((store.root / recipe.id).exists())
      self.assertEqual(list(store.root.glob(".download-*")), [])
      self.assertFalse((store.root / ".artifacts").exists())
      # A cancellation arriving after final integrity verification still prevents commit.
      cancel = False
      verify = Package.verify
      def cancel_after_verification(package):
        nonlocal cancel
        verify(package)
        cancel = True
      with patch("openmodels.client.urlopen", side_effect=lambda url, **kw: BytesIO(blobs[url.rsplit("/", 1)[-1]])), \
           patch.object(Package, "verify", cancel_after_verification):
        with self.assertRaises(DownloadCancelled):
          store.fetch(recipe, cancelled=lambda: cancel)
      self.assertFalse((store.root / recipe.id).exists())
      self.assertEqual(list(store.root.glob(".download-*")), [])

  def test_support_filter_and_missing_downloads(self):
    data, recipe, _ = fixture()
    catalog = Catalog(dumps(data))
    with tempfile.TemporaryDirectory() as root:
      with self.assertRaises(TypeError):
        ModelSwitcher(catalog, root, support=None)
      switcher = ModelSwitcher(catalog, root, support=lambda r: "Requires another host runtime")
      self.assertEqual(switcher.list_models(), [])
      variant = switcher.list_models(include_unsupported=True)[0]["variants"][0]
      self.assertFalse(variant["supported"])
      self.assertIn("another host", variant["reason"])
      switcher.support = lambda r: False
      with self.assertRaises(TypeError):
        switcher.install(recipe.id)
      data["locations"] = {}
      unavailable = ModelSwitcher(Catalog(dumps(data)), root, support=lambda r: None)
      self.assertEqual(unavailable.list_models(), [])
      self.assertIn("not available", unavailable.list_models(include_unsupported=True)[0]["variants"][0]["reason"])

  def test_prepare_requires_installed_verified_supported_package(self):
    data, recipe, blobs = fixture()
    catalog = Catalog(dumps(data), base_url="http://example.test/")
    with tempfile.TemporaryDirectory() as root:
      switcher = ModelSwitcher(catalog, root, support=lambda r: None)
      runner = Mock()
      with self.assertRaises(OSError):
        switcher.prepare(recipe.id, runner, {})
      runner.prepare.assert_not_called()
      with patch("openmodels.client.urlopen", side_effect=lambda url, **kw: BytesIO(blobs[url.rsplit("/", 1)[-1]])):
        package = switcher.install(recipe.id)
      build = SimpleNamespace(recipe=recipe)
      runner.prepare.return_value = build
      self.assertIs(switcher.prepare(recipe.id, runner, {"backend": "example"}), build)
      self.assertEqual(switcher.status["state"], "prepared")
      runner.prepare.assert_called_once_with(package, {"backend": "example"})
      package.artifact("head").write_bytes(b"corrupt")
      runner.prepare.reset_mock()
      with self.assertRaises(ContractError):
        switcher.prepare(recipe.id, runner, {})
      runner.prepare.assert_not_called()
      self.assertIs(switcher.build, build)
      self.assertEqual(switcher.status["state"], "failed")

  def test_example_demo(self):
    result = subprocess.run([sys.executable, "examples/switcher.py", "--demo"],
                            cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=15)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("Example model", result.stdout)
    self.assertIn("Installed and verified", result.stdout)


if __name__ == "__main__":
  unittest.main()
