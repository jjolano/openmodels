"""Optional source/numerical parity check; no device qualification or CPU driving fallback.

DEV=CPU WARP_DEV=CPU python tests/check_reference.py --openpilot /path/to/pinned/openpilot
Add --raw /path/to/retained/output-a.bin to check the existing stock arithmetic oracle.
"""
import argparse
import ast
from pathlib import Path
import sys

import numpy as np

from openmodels.contracts import sha256
from openmodels.profiles import STOCK_SLICES
from openmodels_runner_tinygrad import vendor, constants, parse_model_outputs


def definitions(path):
  return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(Path(path).read_text()).body
          if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--openpilot", type=Path, required=True)
  parser.add_argument("--raw", type=Path)
  args = parser.parse_args()
  source = args.openpilot / "openpilot/selfdrive/modeld"
  for module, name in ((vendor, "compile_modeld.py"), (constants, "constants.py"), (parse_model_outputs, "parse_model_outputs.py")):
    original = definitions(source / name)
    for function, body in definitions(module.__file__).items():
      assert body == original[function], f"vendor drift: {name}:{function}"
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
  if args.raw:
    data = args.raw.read_bytes()
    assert sha256(data) == "de78ac1f136bf0890aaebeb6f07b0b1aa432f5c8a084657b1de45bf006185c48"
    raw_output = np.frombuffer(data, dtype="<f4")
    decoded = parse_model_outputs.Parser().parse_outputs({k: raw_output[np.newaxis, slice(*v)].copy() for k, v in STOCK_SLICES.items()})
    np.testing.assert_allclose(decoded["plan"][0, -1, :3], [14.257, -0.109881, 0.104702], atol=0.001)
    assert all(np.isfinite(v).all() for v in decoded.values())
    print("Retained stock-output oracle accepted; 33-point plan agrees with Moonpilot's reference.")
  print("Pinned source definitions, NV12 packing, and temporal sampling agree.")


if __name__ == "__main__":
  main()
