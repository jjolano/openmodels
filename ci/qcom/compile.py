"""Pinned compiler entrypoint. Run only during explicit off-device preparation."""
import functools
from functools import partial
import hashlib
import importlib.metadata
import importlib.util
from pathlib import Path
import sys

from openmodels import ContractError, Manifest, Package, Recipe
from openmodels.contracts import dumps, loads
from openmodels.profiles import STOCK_INPUT_SHAPES, STOCK_PROFILE, STOCK_SHA256, STOCK_SLICES, TINYGRAD_REVISION
from index.metadata import parse as read_onnx_metadata

TARGET = {"backend": "QCOM", "hardware": "comma3x", "os": "unvalidated",
          "runtime": f"tinygrad@{TINYGRAD_REVISION}",
          "options": {"camera_width": 1928, "camera_height": 1208, "deadline_ms": 50}}


@functools.cache
def host_llvm():
  """The libLLVM the host CPU backend actually loaded, by content.

  The a630 programs come from the pinned qemu toolchain, but the host LLVM is a codegen and
  heuristic input too: the same pinned revision, model and toolchain produced a different
  program set under LLVM 18 and LLVM 21. Unknown host LLVM is an error, never a silent skip.
  """
  from tinygrad.runtime.autogen import llvm

  path = Path(llvm.dll._name)
  if not path.is_file():
    raise ContractError(f"cannot identify the host LLVM library: {path}")
  with path.open("rb") as handle:
    return str(path), hashlib.file_digest(handle, "sha256").hexdigest()


def implementation_digest():
  """Bind the compiler stack: these modules, the SDK, Tinygrad, host LLVM, Python and NumPy."""
  distribution = importlib.metadata.distribution("tinygrad")
  direct = loads(distribution.read_text("direct_url.json") or "{}")
  if direct.get("vcs_info", {}).get("commit_id") != TINYGRAD_REVISION:
    raise ContractError(f"install Tinygrad from the pinned Git revision {TINYGRAD_REVISION}")
  digest = hashlib.sha256()
  sdk = Path(importlib.util.find_spec("openmodels").origin).parent
  tinygrad = Path(importlib.util.find_spec("tinygrad").origin).parent
  for label, directory in (("qcom", Path(__file__).parent), ("openmodels", sdk), ("tinygrad", tinygrad)):
    for path in sorted(directory.rglob("*.py")):
      digest.update(f"{label}/{path.relative_to(directory)}\0".encode())
      digest.update(path.read_bytes())
  digest.update(Path(importlib.util.find_spec("index.metadata").origin).read_bytes())
  digest.update(dumps({"python": sys.version, "numpy": importlib.metadata.version("numpy"),
                       "llvm": host_llvm()[1]}).encode())
  return digest.hexdigest()


def check_target(recipe, target):
  """Only the pinned stock artifact, compiled for the one recorded a630 target."""
  if recipe.profile.id != STOCK_PROFILE.id:
    raise ContractError("unsupported execution profile; archive profiles are not compilable")
  if recipe.data["members"]["supercombo"]["artifact"]["sha256"] != STOCK_SHA256:
    raise ContractError("this compiler supports only the pinned stock artifact")
  if target != TARGET:
    raise ContractError(f"expected exactly the pinned a630 target {TARGET}")


def check_metadata(metadata):
  # ONNX uses [-2:] for padding; compare positions, not slice spelling.
  slices = metadata.get("output_slices") or {}
  if (metadata.get("input_shapes") != STOCK_INPUT_SHAPES or
      metadata.get("output_shapes") != {"outputs": [1, 2576]} or
      {k: slice(*v).indices(2576) for k, v in slices.items()} !=
      {k: slice(*v).indices(2576) for k, v in STOCK_SLICES.items()}):
    raise ContractError("pinned model metadata mismatch")


def compile_package(package_path, output, target_path):
  recipe = Recipe(Manifest((package_path / "recipe.json").read_text()), Manifest((package_path / "profile.json").read_text()))
  package = Package(recipe, package_path)
  package.verify()
  check_target(recipe, loads(target_path.read_bytes()))
  # The metadata parser permits only primitive slices; it cannot execute ONNX metadata.
  metadata = read_onnx_metadata(package.artifact("supercombo"))
  check_metadata(metadata)
  metadata["output_slices"] = {k: slice(*v) for k, v in STOCK_SLICES.items()}
  from tinygrad.nn.onnx import OnnxRunner
  from tinygrad.engine.jit import TinyJit
  from .vendor import (NV12Frame, POLICY_INPUTS, WARP_INPUTS, WARP_DEV, compile_jit, make_input_queues,
                       make_random_images, make_run_policy, make_warp, make_warp_input_queues)
  from .serialization import dump_oob
  model = OnnxRunner(package.artifact("supercombo"))
  result = {"metadata": metadata}
  policy = TinyJit(make_run_policy(model, metadata, 4), prune=True)
  result["run_policy"] = compile_jit(policy,
    partial(make_random_images, keys=["warped"], shape=(2, 6, 128, 256), device=WARP_DEV),
    POLICY_INPUTS, partial(make_input_queues, STOCK_INPUT_SHAPES, 4))
  nv12 = NV12Frame(1928, 1208, 2048, 1216, 608, 4804608)
  result[(1928, 1208)] = compile_jit(TinyJit(make_warp(nv12, 512, 256, 4), prune=True),
    partial(make_random_images, keys=["frame", "big_frame"], shape=nv12.size, device=WARP_DEV),
    WARP_INPUTS, partial(make_warp_input_queues, STOCK_INPUT_SHAPES, 4))
  with (output / "model.pkl").open("wb") as handle:
    dump_oob(result, handle)
