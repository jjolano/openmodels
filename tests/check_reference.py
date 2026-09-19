"""Optional source/numerical parity check for the vendored compiler tooling.

DEV=CPU WARP_DEV=CPU python tests/check_reference.py --openpilot /path/to/pinned/openpilot
"""
import argparse
import ast
from pathlib import Path
import sys

import numpy as np

if __package__ in (None, ""):
  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ci.qcom import vendor


def definitions(path):
  return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(Path(path).read_text()).body
          if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--openpilot", type=Path, required=True)
  args = parser.parse_args()
  original = definitions(args.openpilot / "openpilot/selfdrive/modeld/compile_modeld.py")
  for function, body in definitions(vendor.__file__).items():
    assert body == original[function], f"vendor drift: compile_modeld.py:{function}"
  from tinygrad import Tensor
  # Identity NV12 warp independently checked against the six-plane packing definition.
  raw = np.arange(192, dtype=np.uint8)
  nv12 = vendor.NV12Frame(16, 8, 16, 8, 4, 192)
  prepared = vendor.make_frame_prepare(nv12, 16, 8)(Tensor(raw), Tensor(np.eye(3, dtype=np.float32))).numpy()
  y, uv = raw[:128].reshape(8, 16), raw[128:].reshape(4, 16)
  expected = np.stack([y[0::2, 0::2], y[1::2, 0::2], y[0::2, 1::2], y[1::2, 1::2], uv[:, 0::2], uv[:, 1::2]])
  np.testing.assert_array_equal(prepared, expected)
  queue = Tensor(np.zeros((5, 1), np.float32)).contiguous().realize()
  for i in range(1, 7):
    sampled = vendor.shift_and_sample(queue, Tensor([[float(i)]]), lambda q: vendor.sample_skip(q, 4)).numpy()
  np.testing.assert_array_equal(sampled, np.array([[2, 6]], np.float32))
  print("Pinned source definitions, NV12 packing, and temporal sampling agree.")


if __name__ == "__main__":
  main()
