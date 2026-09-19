"""Compile-target and metadata checks for the off-device a630 tooling."""
from copy import deepcopy
import unittest

from openmodels import ContractError, Manifest, Recipe
from openmodels.profiles import STOCK_INPUT_SHAPES, STOCK_PROFILE, STOCK_SHA256, STOCK_SLICES
from ci.qcom.compile import TARGET, check_metadata, check_target


def stock_recipe(artifact=STOCK_SHA256, profile=STOCK_PROFILE):
  return Recipe(Manifest.create({"schema": 1, "type": "recipe", "profile": profile.id,
    "members": {"supercombo": {"artifact": {"sha256": artifact, "size": 60881999, "format": "onnx"},
      "source": {"repository": "commaai/openpilot", "license": "MIT"}, "configuration": {}, "missing": [],
      "inputs": {}, "outputs": {}, "targets": ["QCOM"], "metadata": {}}},
    "configuration": {"frame_skip": 4, "LAT_SMOOTH_SECONDS": 0, "LONG_SMOOTH_SECONDS": 0.3}}), profile)


class CompileTargetTests(unittest.TestCase):
  def test_only_the_pinned_artifact_profile_and_target_compile(self):
    check_target(stock_recipe(), dict(TARGET))
    for key, value in (("backend", "CPU"), ("runtime", "tinygrad@latest"), ("hardware", "comma4"),
                       ("os", "agnos-test-build")):
      invalid = dict(TARGET)
      invalid[key] = value
      with self.assertRaises(ContractError):
        check_target(stock_recipe(), invalid)
    changed_options = dict(TARGET)
    changed_options["options"] = {**TARGET["options"], "deadline_ms": 100}
    with self.assertRaises(ContractError):
      check_target(stock_recipe(), changed_options)
    with self.assertRaisesRegex(ContractError, "pinned stock artifact"):
      check_target(stock_recipe(artifact="a" * 64), dict(TARGET))
    other = Manifest.create({"schema": 1, "type": "profile", "name": "example/other/v1",
      "slot_sets": [["supercombo"]], "connections": [], "required_configuration": [],
      "inputs": {}, "outputs": {}, "state": {}, "implementation_source": {}})
    with self.assertRaisesRegex(ContractError, "unsupported execution profile"):
      check_target(stock_recipe(profile=other), dict(TARGET))

  def test_metadata_accepts_equivalent_padding_only(self):
    metadata = {"input_shapes": deepcopy(STOCK_INPUT_SHAPES), "output_shapes": {"outputs": [1, 2576]},
                "output_slices": deepcopy(STOCK_SLICES)}
    metadata["output_slices"]["pad"] = [-2, None, None]
    check_metadata(metadata)
    for field, value in (("padding", [-3, None, None]), ("output_length", 2577)):
      with self.subTest(field=field):
        invalid = deepcopy(metadata)
        if field == "padding":
          invalid["output_slices"]["pad"] = value
        else:
          invalid["output_shapes"]["outputs"][1] = value
        with self.assertRaisesRegex(ContractError, "pinned model metadata mismatch"):
          check_metadata(invalid)


if __name__ == "__main__":
  unittest.main()
