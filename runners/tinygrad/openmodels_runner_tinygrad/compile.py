"""Pinned compiler entrypoint. Run only during explicit local preparation."""
from functools import partial
from pathlib import Path
import sys

from openmodels import ContractError, Manifest, Package, Recipe
from openmodels.contracts import loads
from openmodels.profiles import STOCK_INPUT_SHAPES, STOCK_SLICES
from index.metadata import parse as read_onnx_metadata


def main():
  package_path, output, target_path = map(Path, sys.argv[1:])
  recipe = Recipe(Manifest((package_path / "recipe.json").read_text()), Manifest((package_path / "profile.json").read_text()))
  package = Package(recipe, package_path)
  package.verify()
  from .runner import check_recipe
  check_recipe(recipe, loads(target_path.read_bytes()))
  # The metadata parser permits only primitive slices; it cannot execute ONNX metadata.
  metadata = read_onnx_metadata(package.artifact("supercombo"))
  if metadata.get("input_shapes") != STOCK_INPUT_SHAPES or metadata.get("output_slices") != STOCK_SLICES:
    raise ContractError("pinned model metadata mismatch")
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


if __name__ == "__main__":
  main()
