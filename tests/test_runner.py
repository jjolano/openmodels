"""Session state checks use a fake executable, never a CPU driving fallback."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np

from openmodels import ContractError, Manifest, Recipe
from openmodels.profiles import STOCK_INPUT_SHAPES, STOCK_PROFILE, STOCK_SHA256, STOCK_SLICES, TINYGRAD_REVISION
from openmodels_runner_tinygrad import session
from openmodels_runner_tinygrad.compile import check_metadata
from openmodels_runner_tinygrad.runner import check_recipe


def stock_recipe():
  return Recipe(Manifest.create({"schema": 1, "type": "recipe", "profile": STOCK_PROFILE.id,
    "members": {"supercombo": {"artifact": {"sha256": STOCK_SHA256, "size": 60881999, "format": "onnx"},
      "source": {"repository": "commaai/openpilot", "license": "MIT"}, "configuration": {}, "missing": [],
      "inputs": {}, "outputs": {}, "targets": ["QCOM"], "metadata": {}}},
    "configuration": {"frame_skip": 4, "LAT_SMOOTH_SECONDS": 0, "LONG_SMOOTH_SECONDS": 0.3}}), STOCK_PROFILE)


def target():
  return {"backend": "QCOM", "hardware": "comma3x", "os": "agnos-test-build", "runtime": f"tinygrad@{TINYGRAD_REVISION}",
          "options": {"camera_width": 1928, "camera_height": 1208, "deadline_ms": 50}}


class Engine:
  def __init__(self):
    self.bad = False
    self.desires = []

  def queues(self):
    self.npy = {"desire": np.zeros(8, np.float32), "traffic_convention": np.zeros((1, 2), np.float32),
                "action_t": np.zeros((1, 2), np.float32), "prev_feat": np.zeros((1, 512), np.float32),
                "tfm": np.zeros((3, 3), np.float32), "big_tfm": np.zeros((3, 3), np.float32)}
    return {k: None for k in ("img_q", "big_img_q", "feat_q", "desire_q", "packed_npy_inputs", "tfm", "big_tfm")}, self.npy

  def policy(self, **kwargs):
    self.desires.append(self.npy["desire"].copy())
    raw = np.zeros((1, 2576), dtype=np.float32)
    raw[0, 1064:1576] = self.npy["prev_feat"] + 1
    raw[0, 2566:2574] = np.arange(8, dtype=np.float32)
    if self.bad:
      raw[0, 0] = np.nan
    class Tensor:
      def numpy(self):
        return raw
    return (Tensor(),)

  def executable(self):
    return {"metadata": {"input_shapes": STOCK_INPUT_SHAPES, "output_slices": {k: slice(*v) for k, v in STOCK_SLICES.items()}},
            "run_policy": self.policy, (1928, 1208): lambda **kwargs: None}


class RunnerTests(unittest.TestCase):
  def setUp(self):
    self.engine = Engine()
    self.patch_queues = patch.object(session, "new_queues", self.engine.queues)
    self.patch_frames = patch.object(session, "frame_tensor", lambda frame: frame.data)
    self.patch_queues.start()
    self.patch_frames.start()
    self.addCleanup(self.patch_queues.stop)
    self.addCleanup(self.patch_frames.stop)
    self.runner = session.DrivingSession(self.engine.executable(), "a" * 64, clock_ns=lambda: 0)
    self.addCleanup(self.runner.close)
    self.frame = memoryview(np.zeros(4804608, dtype=np.uint8))

  def inputs(self, n, *, desire=1):
    pulse = np.zeros(8, dtype=np.float32)
    pulse[desire] = 1
    frame = session.Frame(self.frame, n, 1 + n * 50000000)
    return session.DrivingInputs(frame, frame, np.eye(3, dtype=np.float32), np.eye(3, dtype=np.float32),
                                pulse, np.array([[1, 0]], dtype=np.float32), np.zeros((1, 2), dtype=np.float32))

  def test_warmup_state_feedback_owned_outputs_and_reset(self):
    for n in range(4):
      self.assertIsNone(self.runner.step(self.inputs(n)))
    result = self.runner.step(self.inputs(4))
    self.assertEqual(result.plan.mean.shape, (1, 33, 15))
    np.testing.assert_equal(result.raw[1064:1576], np.full(512, 5))
    np.testing.assert_equal(result.raw[2566:2574], np.arange(8))
    self.assertAlmostEqual(result.desire_state.sum(), 1.0, places=6)
    self.assertEqual(self.engine.desires[1][1], 1)
    self.assertEqual(self.engine.desires[2][1], 0)
    self.runner.step(self.inputs(5))
    np.testing.assert_equal(result.raw[1064:1576], np.full(512, 5))
    self.runner.reset()
    self.assertIsNone(self.runner.step(self.inputs(100)))
    self.assertEqual(self.engine.desires[-1][1], 1)
    self.assertEqual(self.engine.npy["prev_feat"][0, 0], 1)

  def test_discontinuity_and_nonfinite_output_invalidate_state(self):
    self.runner.step(self.inputs(0))
    with self.assertRaisesRegex(ContractError, "discontinuity"):
      self.runner.step(self.inputs(2))
    self.assertEqual(self.runner.count, 0)
    self.engine.bad = True
    with self.assertRaisesRegex(ContractError, "nonfinite"):
      self.runner.step(self.inputs(3))
    self.assertEqual(self.runner.count, 0)
    self.engine.bad = False
    self.assertIsNone(self.runner.step(self.inputs(4)))

  def test_deadline_and_input_validation(self):
    ticks = iter([0, 50000001])
    self.runner.clock_ns = lambda: next(ticks)
    with self.assertRaisesRegex(ContractError, "deadline"):
      self.runner.step(self.inputs(0))
    self.assertEqual(self.runner.count, 0)
    self.runner.clock_ns = lambda: 0
    inputs = self.inputs(1)
    inputs.road_transform[2] = 0
    with self.assertRaisesRegex(ContractError, "singularity"):
      self.runner.step(inputs)
    self.runner.close()
    self.runner.close()
    with self.assertRaisesRegex(ContractError, "closed"):
      self.runner.step(self.inputs(2))

  def test_exact_profile_and_target_required(self):
    recipe = stock_recipe()
    check_recipe(recipe, target())
    for key, value in (("backend", "CPU"), ("runtime", "latest"), ("hardware", "comma4")):
      invalid = target()
      invalid[key] = value
      with self.assertRaises(ContractError):
        check_recipe(recipe, invalid)
    profile = recipe.profile.data
    profile["state"]["frame_skip"] = 1
    profile = Manifest.create(profile)
    changed = recipe.data
    changed["profile"] = profile.id
    with self.assertRaisesRegex(ContractError, "unsupported execution profile"):
      check_recipe(Recipe(Manifest.create(changed), profile), target())

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
