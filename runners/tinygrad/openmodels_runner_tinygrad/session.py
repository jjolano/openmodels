"""Typed synchronous driving session. Frame acquisition and activation belong to the host."""
from dataclasses import dataclass
from time import perf_counter_ns

import numpy as np
from numpy.typing import NDArray

from openmodels import ContractError
from openmodels.profiles import STOCK_INPUT_SHAPES, STOCK_SLICES
from .parse_model_outputs import Parser


@dataclass(frozen=True)
class Frame:
  data: memoryview
  frame_id: int
  timestamp_ns: int


@dataclass(frozen=True)
class DrivingInputs:
  road: Frame
  wide: Frame
  road_transform: NDArray[np.float32]
  wide_transform: NDArray[np.float32]
  desire: NDArray[np.float32]
  traffic_convention: NDArray[np.float32]
  action_t: NDArray[np.float32]


@dataclass(frozen=True)
class Prediction:
  mean: NDArray[np.float32]
  std: NDArray[np.float32]


@dataclass(frozen=True)
class DrivingOutput:
  frame_id: int
  timestamp_ns: int
  build_id: str
  elapsed_ns: int
  raw: NDArray[np.float32]
  plan: Prediction
  lane_lines: Prediction
  road_edges: Prediction
  lead: Prediction
  pose: Prediction
  wide_from_device_euler: Prediction
  road_transform: Prediction
  meta: NDArray[np.float32]
  desire_pred: NDArray[np.float32]
  desire_state: NDArray[np.float32]
  lane_lines_prob: NDArray[np.float32]
  lead_prob: NDArray[np.float32]


def new_queues():
  from .vendor import make_input_queues
  return make_input_queues(STOCK_INPUT_SHAPES, 4, device="QCOM")


def frame_tensor(frame):
  from tinygrad.tensor import Tensor
  # ponytail: copies ~9.6MB per pair; add qualified external-buffer imports if target timing requires them.
  return Tensor(np.frombuffer(frame.data, dtype=np.uint8).copy(), device="QCOM").realize()


class DrivingSession:
  def __init__(self, executable, build_id, *, clock_ns=perf_counter_ns):
    metadata = executable["metadata"]
    slices = {k: slice(*v) for k, v in STOCK_SLICES.items()}
    if metadata["input_shapes"] != STOCK_INPUT_SHAPES or metadata["output_slices"] != slices:
      raise ContractError("compiled model metadata does not match the pinned profile")
    self.executable, self.build_id, self.clock_ns = executable, build_id, clock_ns
    self.closed = False
    self.parser = Parser()
    self.reset()
    # Allocate/capture runtime buffers before the host begins scheduling real inputs.
    dummy = memoryview(np.zeros(4804608, dtype=np.uint8))
    identity = np.eye(3, dtype=np.float32)
    self._evaluate(DrivingInputs(Frame(dummy, 0, 1), Frame(dummy, 0, 1), identity, identity,
                                np.zeros(8, dtype=np.float32), np.array([[1, 0]], dtype=np.float32),
                                np.zeros((1, 2), dtype=np.float32)))
    self.reset()

  def reset(self):
    if self.closed:
      raise ContractError("session is closed")
    self.queues, self.npy = new_queues()
    self.prev_desire = np.zeros(8, dtype=np.float32)
    self.previous_id, self.previous_time, self.count = None, None, 0

  def close(self):
    self.closed = True
    self.executable = self.queues = self.npy = None

  def __enter__(self):
    return self

  def __exit__(self, *args):
    self.close()

  def _validate(self, inputs):
    for frame in (inputs.road, inputs.wide):
      if (type(frame.frame_id) is not int or frame.frame_id < 0 or type(frame.timestamp_ns) is not int
          or frame.timestamp_ns <= 0):
        raise ContractError("invalid frame identity or timestamp")
      view = memoryview(frame.data)
      if not view.c_contiguous or view.nbytes != 4804608:
        raise ContractError("frame must contain the complete pinned NV12 allocation")
    if inputs.road.frame_id != inputs.wide.frame_id or abs(inputs.road.timestamp_ns - inputs.wide.timestamp_ns) > 10000000:
      raise ContractError("camera frames are not synchronized")
    if self.previous_id is not None and (inputs.road.frame_id != self.previous_id + 1 or
        not 25000000 <= inputs.road.timestamp_ns - self.previous_time <= 75000000):
      raise ContractError("frame discontinuity; state reset")
    for value, shape in ((inputs.road_transform, (3, 3)), (inputs.wide_transform, (3, 3)),
                         (inputs.desire, (8,)), (inputs.traffic_convention, (1, 2)), (inputs.action_t, (1, 2))):
      if not isinstance(value, np.ndarray) or value.dtype != np.float32 or value.shape != shape or not np.isfinite(value).all():
        raise ContractError("invalid profile input shape, dtype, or nonfinite value")
    for transform in (inputs.road_transform, inputs.wide_transform):
      denominators = [float(transform[2] @ np.array([x, y, 1])) for x in (0, 511) for y in (0, 255)]
      if min(denominators) <= 0 <= max(denominators):
        raise ContractError("transform crosses a projective singularity")
    if not np.isin(inputs.desire, [0, 1]).all() or inputs.desire.sum() > 1:
      raise ContractError("desire must be zero or one-hot")
    if not np.isin(inputs.traffic_convention, [0, 1]).all() or inputs.traffic_convention.sum() != 1:
      raise ContractError("traffic convention must be one-hot")
    if (inputs.action_t < 0).any():
      raise ContractError("action delays must be nonnegative")

  def _evaluate(self, inputs):
    desire = inputs.desire.copy()
    desire[0] = 0
    self.npy["desire"][:] = np.where(desire - self.prev_desire > .99, desire, 0)
    self.prev_desire[:] = desire
    self.npy["traffic_convention"][:] = inputs.traffic_convention
    self.npy["action_t"][:] = inputs.action_t
    self.npy["tfm"][:] = inputs.road_transform
    self.npy["big_tfm"][:] = inputs.wide_transform
    warped = self.executable[(1928, 1208)](tfm=self.queues["tfm"], big_tfm=self.queues["big_tfm"],
                 frame=frame_tensor(inputs.road), big_frame=frame_tensor(inputs.wide))
    outs, = self.executable["run_policy"](**{k: self.queues[k] for k in (
      "img_q", "big_img_q", "feat_q", "desire_q", "packed_npy_inputs")}, warped=warped)
    raw = outs.numpy()[0].copy()
    if raw.shape != (2576,) or raw.dtype != np.float32 or not np.isfinite(raw).all():
      raise ContractError("invalid or nonfinite model output")
    self.npy["prev_feat"][:] = raw[1064:1576]
    return raw

  def step(self, inputs: DrivingInputs) -> DrivingOutput | None:
    if self.closed:
      raise ContractError("session is closed")
    started = self.clock_ns()
    try:
      self._validate(inputs)
      raw = self._evaluate(inputs)
      # The upstream parser mutates categorical slices. Keep the raw result independently owned.
      decoded = self.parser.parse_outputs({k: raw[np.newaxis, slice(*v)].copy() for k, v in STOCK_SLICES.items()})
      if not all(np.isfinite(value).all() for value in decoded.values()):
        raise ContractError("nonfinite decoded output")
      elapsed = self.clock_ns() - started
      if elapsed < 0 or elapsed > 50000000:
        raise ContractError("inference deadline exceeded; state reset")
      self.previous_id, self.previous_time = inputs.road.frame_id, inputs.road.timestamp_ns
      self.count += 1
      if self.count < 5:
        return None
      return DrivingOutput(inputs.road.frame_id, inputs.road.timestamp_ns, self.build_id, elapsed, raw,
        **{name: Prediction(decoded[name], decoded[name + "_stds"]) for name in (
          "plan", "lane_lines", "road_edges", "lead", "pose", "wide_from_device_euler", "road_transform")},
        **{name: decoded[name] for name in ("meta", "desire_pred", "desire_state", "lane_lines_prob", "lead_prob")})
    except Exception:
      self.reset()
      raise
