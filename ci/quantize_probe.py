"""Measure what int8 quantization of a catalog model actually buys.

Experimental host-only harness in the style of `ci/cross_compile.py`: no CI wiring, no new
third-party dependency, writes only under `--out`. It fetches one recipe through the ordinary
verified client, inventories the graph, quantizes weights with ONNX Runtime, and then measures
what the pinned Tinygrad revision makes of the result (import, a630 compile through the QCOM
bridge, host numerics).

Nothing here publishes an artifact or qualifies a model. A quantized derivative is a new
unqualified artifact; it never reclassifies an archived model, and this probe does not touch
recipe identity, digests or model class.

    python -m ci.quantize_probe --recipe <digest-or-name> --out DIR
"""
import argparse
from collections import Counter
import hashlib
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import time

from ci.cross_compile import TOOLCHAIN_SHA256, TOOLCHAIN_URL, check_bridge, install_bridge
from openmodels import Catalog, ModelStore
from openmodels.contracts import dumps
from openmodels.profiles import TINYGRAD_REVISION

PHASES = ("inventory", "quantize", "import", "compile", "numerics")
# Host-only phases run on the host CPU; the process environment is the QCOM compile environment
# (ci/cross_compile.py parity), so every phase that must stay off the accelerator pins the device.
HOST_DEVICE = "CPU:LLVM"
COMPILE_ENV = {"DEV": f"QCOM;{HOST_DEVICE}", "WARP_DEV": "QCOM", "BEAM": "0", "IMAGE": "0",
               "JIT_BATCH_SIZE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
QUANTIZED_OPS = ("MatMulInteger", "ConvInteger", "DynamicQuantizeLinear", "QuantizeLinear", "DequantizeLinear")


def skipped(reason):
  return {"skipped": reason}


def measured(phase):
  """Run one measurement phase; a refusal by the toolchain is recorded, not raised."""
  try:
    return phase()
  except Exception as exc:
    return {"failed": _error(exc)}


def inventory(path):
  """Descriptive record of one ONNX file: shapes and opsets from `index.metadata`, payload bytes here."""
  import numpy as np
  import onnx

  from index.metadata import parse

  record = parse(str(path))
  model = onnx.load(str(path), load_external_data=False)
  graph = model.graph
  consumers: dict[str, list[str]] = {}
  for node in graph.node:
    for name in node.input:
      consumers.setdefault(name, []).append(node.op_type)
  by_dtype: dict[str, int] = {}
  by_consumer_op: dict[str, int] = {}
  initializer_bytes = 0
  for tensor in graph.initializer:
    element = np.dtype(onnx.helper.tensor_dtype_to_np_dtype(tensor.data_type)).itemsize
    payload = len(tensor.raw_data) or element * int(np.prod(tensor.dims, dtype=np.int64))
    initializer_bytes += payload
    dtype = onnx.TensorProto.DataType.Name(tensor.data_type).lower()
    by_dtype[dtype] = by_dtype.get(dtype, 0) + payload
    # A weight consumed by several nodes is attributed to each of them; supercombo weights have one consumer.
    for op_type in consumers.get(tensor.name, ()):
      by_consumer_op[op_type] = by_consumer_op.get(op_type, 0) + payload
  op_counts = Counter(node.op_type for node in graph.node)
  macs, macs_by_op, unresolved = _macs(model)
  record.update({
    "file_bytes": path.stat().st_size,
    "initializer_bytes": initializer_bytes,
    "initializer_count": len(graph.initializer),
    "node_count": len(graph.node),
    "op_counts": dict(op_counts),
    "by_dtype": by_dtype,
    "by_consumer_op": by_consumer_op,
    # int8 floor for fp16 weights: the smallest this graph could weigh with 8-bit weights.
    "weight_floor_bytes": by_dtype.get("float16", 0) // 2,
    "macs": macs,
    "macs_by_op": macs_by_op,
    "macs_unresolved_nodes": unresolved,
  })
  return record


def _macs(model):
  """Multiply-accumulates per inference for the weight-bearing ops, from inferred shapes.

  Conv counts output elements times the per-output kernel volume; MatMul treats a 2-D right operand
  as broadcast over the batch; Gemm follows transA/transB. Everything else is left out, so this is
  the arithmetic floor, not a full op count. The model is normalized in place: the archived graphs
  carry duplicate opset imports and `value_info` that contradicts inference.
  """
  import numpy as np
  import onnx

  normalized(model)
  try:
    inferred = onnx.shape_inference.infer_shapes(model, strict_mode=False)
  except Exception:
    return None, None, None
  graph = inferred.graph
  shapes = {v.name: tuple(dim.dim_value if dim.HasField("dim_value") else 0 for dim in v.type.tensor_type.shape.dim)
            for v in [*graph.input, *graph.output, *graph.value_info]}
  shapes.update({tensor.name: tuple(tensor.dims) for tensor in graph.initializer})
  total, by_op, unresolved = 0, {}, 0
  for node in graph.node:
    dimensions = [shapes.get(name) for name in node.input]
    count = None
    if node.op_type == "Conv" and len(dimensions) >= 2 and all(dimensions[:2]) and len(dimensions[0]) >= 4:
      count = int(np.prod(dimensions[0])) * int(np.prod(dimensions[1][1:]))
    elif node.op_type == "MatMul" and len(dimensions) >= 2 and all(dimensions[:2]):
      left, right = dimensions[0], dimensions[1]
      if len(left) >= 2 and len(right) >= 2:
        count = int(np.prod(left[:-1])) * left[-1] * right[-1]
    elif node.op_type == "Gemm" and len(dimensions) >= 2 and all(dimensions[:2]):
      left, right = dimensions[0], dimensions[1]
      opts = {attr.name: attr for attr in node.attribute}
      rows, inner = (left[1], left[0]) if getattr(opts.get("transA"), "i", 0) else (left[0], left[1])
      columns = right[0] if getattr(opts.get("transB"), "i", 0) else right[1]
      count = rows * inner * columns
    if count is None:
      if node.op_type in ("Conv", "MatMul", "Gemm"):
        unresolved += 1
      continue
    total += count
    by_op[node.op_type] = by_op.get(node.op_type, 0) + count
  return total, by_op, unresolved


def quantize(source, destination, source_dtype, operators):
  """Weight-only int8 dynamic quantization over Conv/Gemm/MatMul, with no calibration data.

  The archived openpilot graphs are not quantizable as they stand: `onnx.compose.merge_models`
  left the same ai.onnx opset declared four times, and the recorded `value_info` contradicts
  shape inference. Worse, ONNX Runtime accepts a float16 graph and silently emits a graph that
  is no longer legal ONNX (`DynamicQuantizeLinear` on float16 activations), so each attempt is
  checked against the op type constraints before it is kept. These attempts escalate over
  working copies and record which one produced a valid artifact.
  """
  from onnxruntime.quantization import QuantType, quantize_dynamic

  short = "fp16" if source_dtype == "float16" else "fp32"
  working = destination.with_name(destination.stem + "-working.onnx")
  staged = destination.with_name(destination.stem + "-staged.onnx")
  errors: dict[str, str] = {}
  for label, normalizations, fp32 in ((f"dynamic-{short}", [], False),
                                      (f"dynamic-{short}-normalized", ["opset-import-dedupe", "value_info-drop"], False),
                                      ("dynamic-fp32-rewrite", ["opset-import-dedupe", "value_info-drop", "float16-to-float32"], True)):
    if fp32 and source_dtype != "float16":
      continue
    model = source
    if normalizations:
      rewrite_normalized(source, working, fp32=fp32)
      model = working
    try:
      quantize_dynamic(str(model), str(staged), weight_type=QuantType.QInt8, per_channel=True,
                       op_types_to_quantize=["Conv", "Gemm", "MatMul"])
    except Exception as exc:  # the failure mode is part of the measurement
      errors[label] = _error(exc)
      continue
    if (invalid := type_error(staged)) is not None:
      errors[label] = invalid
      continue
    os.replace(staged, destination)
    return {"path_used": label, "normalizations": normalizations, "attempt_errors": errors}
  failed = list(errors)[-1]
  return {"path_used": failed_op(failed, errors[failed], operators), "normalizations": [], "attempt_errors": errors}


def type_error(path):
  """Reason the graph violates its own op type constraints, or None when it is legal ONNX."""
  import onnx

  try:
    onnx.shape_inference.infer_shapes(onnx.load(str(path), load_external_data=False), strict_mode=True, check_type=True)
    return None
  except Exception as exc:
    return _error(exc)


def normalized(model, *, fp32=False):
  """In place: one opset import per domain, no advisory value_info, optionally every fp16 tensor fp32."""
  import numpy as np
  from onnx import TensorProto, numpy_helper

  graph = model.graph
  seen, imports = set(), []
  for opset in model.opset_import:
    if (opset.domain, opset.version) not in seen:
      seen.add((opset.domain, opset.version))
      imports.append(opset)
  del model.opset_import[:]
  model.opset_import.extend(imports)
  del graph.value_info[:]
  if not fp32:
    return model
  for tensor in graph.initializer:
    if tensor.data_type == TensorProto.FLOAT16:
      tensor.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(tensor).astype(np.float32), tensor.name))
  for value in [*graph.input, *graph.output]:
    if value.type.HasField("tensor_type") and value.type.tensor_type.elem_type == TensorProto.FLOAT16:
      value.type.tensor_type.elem_type = TensorProto.FLOAT
  for node in graph.node:
    if node.op_type == "Cast":
      for attribute in node.attribute:
        if attribute.name == "to" and attribute.i == TensorProto.FLOAT16:
          attribute.i = TensorProto.FLOAT
  return model


def rewrite_normalized(source, destination, *, fp32):
  """Working copy of `source` written to `destination`."""
  import onnx

  onnx.save(normalized(onnx.load(str(source)), fp32=fp32), str(destination))


def failed_op(label, error, operators):
  """Name the graph op the runtime complained about, if it says so."""
  names = [name.split("::")[-1] for name in operators] + list(QUANTIZED_OPS)
  return f"{label}-failed:{next((name for name in names if name in error), 'unknown')}"


def _error(exc):
  return f"{type(exc).__name__}: {exc}"


def import_graph(path):
  """Whether the pinned Tinygrad revision can even parse this graph: no execution, host CPU only."""
  from tinygrad.helpers import Context

  with Context(DEV=HOST_DEVICE):
    try:
      from tinygrad.nn.onnx import OnnxRunner
      OnnxRunner(str(path))
      return {"ok": True}
    except Exception as exc:
      return {"ok": False, "error": _error(exc)}


def build_inputs(metadata, seed):
  """Deterministic inputs in the dtypes the pinned runtime feeds: uint8 camera frames, float32 else.

  The graph declares float16 for everything but the images, but both `examples/openpilot/compile3.py`
  ("Float inputs and outputs to tinyjits for openpilot are always float32") and the pinned runner
  feed float32 there, and the int8 derivative cannot be lowered at all with float16 activations.
  """
  import numpy as np

  rng = np.random.default_rng(seed)
  inputs = {}
  for name, shape in metadata["input_shapes"].items():
    dims = tuple(dim if isinstance(dim, int) else 1 for dim in shape)
    dtype = np.uint8 if "img" in name else np.float32
    inputs[name] = (rng.integers(0, 256, dims, dtype=dtype) if dtype is np.uint8
                    else rng.standard_normal(dims).astype(dtype))
  return inputs


def run_graph(path, inputs):
  """One inference through the pinned Tinygrad ONNX runner, on the host CPU."""
  from tinygrad import Tensor
  from tinygrad.device import Device
  from tinygrad.nn.onnx import OnnxRunner

  runner = OnnxRunner(str(path))
  outputs = runner({name: Tensor(value).to(Device.DEFAULT) for name, value in inputs.items()})
  return next(iter(outputs.values())).cast("float32").numpy().reshape(-1).astype("float64")


def numerics(source, quantized, metadata, seed):
  """Numeric divergence between the archived graph and its quantized derivative."""
  from tinygrad.helpers import Context

  inputs = build_inputs(metadata, seed)
  with Context(DEV=HOST_DEVICE):
    reference = run_graph(source, inputs)
    candidate = run_graph(quantized, inputs)
  if reference.shape != candidate.shape:
    return {"seed": seed, "skipped": f"output shapes differ: {reference.shape} vs {candidate.shape}"}
  result = {"seed": seed, **_compare(reference, candidate)}
  plan = (metadata.get("output_slices") or {}).get("plan")
  window = slice(*plan) if plan is not None else None
  result["plan"] = _compare(reference[window], candidate[window]) if window and (window.stop or 0) <= reference.shape[0] else None
  return result


def _compare(reference, candidate):
  import numpy as np

  difference = reference - candidate
  reference_norm = float(np.linalg.norm(reference))
  candidate_norm = float(np.linalg.norm(candidate))
  return {
    "max_abs_diff": _finite(np.max(np.abs(difference))),
    "rel_l2": _finite(float(np.linalg.norm(difference)) / reference_norm) if reference_norm else None,
    "cosine": _finite(float(np.dot(reference, candidate)) / (reference_norm * candidate_norm))
              if reference_norm and candidate_norm else None,
  }


def _finite(value):
  value = float(value)
  return value if math.isfinite(value) else None


def compile_qcom(path, metadata, seed):
  """Compile one graph for the a630 through the documented host bridge, then count its kernels."""
  if platform.system() != "Linux" or platform.machine() != "x86_64":
    return skipped("QCOM cross-compilation needs Linux x86-64")
  qemu = shutil.which("qemu-aarch64-static")
  if qemu is None and shutil.which("docker") is None:
    return skipped("no qemu-aarch64-static or docker for linux/aarch64")
  import tinygrad
  # The ARM python in the upstream compiler sysroot must import this exact Tinygrad; set before
  # the first QCOM compile spawns that compiler process.
  os.environ["PYTHONPATH"] = str(Path(tinygrad.__file__).parent.parent)
  check_bridge()
  from tinygrad import Tensor, TinyJit
  from tinygrad.device import Device
  from tinygrad.nn.onnx import OnnxRunner
  from tinygrad.uop.ops import Ops

  run_onnx = OnnxRunner(str(path))
  # Same shape as examples/openpilot/compile3.py: one run on the target, one to capture the graph.
  jit = TinyJit(lambda **kw: next(iter(run_onnx({k: v.to(Device.DEFAULT) for k, v in kw.items()}).values())).cast('float32'),
                prune=True)
  inputs = {name: Tensor(value) for name, value in build_inputs(metadata, seed).items()}
  started = time.perf_counter()
  jit(**inputs)
  jit(**inputs)
  seconds = time.perf_counter() - started
  programs = [u for u in jit.captured.linear.toposort() if u.op is Ops.PROGRAM]
  return {"programs": len(programs), "kernel_bytes": sum(len(program.to_elf().lib) for program in programs),
          "seconds": round(seconds, 3), "emulation": "qemu" if qemu else "docker"}


def versions():
  from importlib.metadata import version
  return {name: version(name) for name in ("onnx", "onnxruntime", "numpy")}


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--recipe", required=True, help="recipe digest, or a name resolving to exactly one recipe")
  parser.add_argument("--out", type=Path, required=True, help="output directory; created if absent")
  parser.add_argument("--store", type=Path, default=Path("/var/tmp/openmodels-quant/store"))
  parser.add_argument("--catalog", default="https://jjolano.github.io/openmodels/catalog.json")
  parser.add_argument("--stock-bytes", type=int, default=None, help="size of the standard artifact, for a size ratio")
  parser.add_argument("--phase", action="append", choices=PHASES, dest="phases", help="repeatable; default all, in order")
  parser.add_argument("--seed", type=int, default=0)
  args = parser.parse_args()
  phases = tuple(dict.fromkeys(args.phases)) if args.phases else PHASES
  # Set before the first Tinygrad import, which any phase may trigger.
  os.environ.update(COMPILE_ENV)
  # Tinygrad's pattern matchers capture the module globals they call on first use, so the QCOM
  # bridge has to be in place before *any* kernel runs in this process, not just before the
  # compile phase; ci/cross_compile.py gets this for free because its whole process is one build.
  install_bridge()
  args.out.mkdir(parents=True, exist_ok=True)

  catalog = Catalog.load(args.catalog)
  recipe = catalog.resolve(args.recipe)
  role = next(iter(recipe.data["members"]))
  member = recipe.data["members"][role]["artifact"]
  source = ModelStore(args.store, catalog).fetch(recipe).artifact(role)
  quantized = args.out / (source.stem + ".int8.onnx")
  metadata = inventory(source)
  source_dtype = "float16" if metadata["by_dtype"].get("float16") else "float32"

  report = {"experimental": True, "qualified": False, "catalog": {"revision": catalog.revision},
            "recipe": recipe.id, "role": role,
            "source": {"sha256": member["sha256"], "size": member["size"], "path": str(source)},
            "toolchain": {"url": TOOLCHAIN_URL, "sha256": TOOLCHAIN_SHA256},
            "tinygrad": {"revision": TINYGRAD_REVISION}, "python": sys.version, "versions": versions(),
            "inventory": metadata if "inventory" in phases else skipped("phase not requested")}

  report["quantized"] = skipped("phase not requested")
  if "quantize" in phases:
    outcome = quantize(source, quantized, source_dtype, metadata["operators"])
    report["quantized"] = {"path": str(quantized), **outcome}
    if quantized.exists():
      with quantized.open("rb") as handle:
        report["quantized"]["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
      result = inventory(quantized)
      report["quantized"].update({
        "size": quantized.stat().st_size,
        "inventory": result,
        "ops_added": {op: result["op_counts"].get(op, 0) for op in QUANTIZED_OPS},
        "ratio_vs_source": round(quantized.stat().st_size / member["size"], 6),
      })
      if args.stock_bytes:
        report["quantized"]["ratio_vs_stock"] = round(quantized.stat().st_size / args.stock_bytes, 6)

  present = quantized.exists()
  report["import"] = skipped("phase not requested")
  if "import" in phases:
    report["import"] = {"source": import_graph(source),
                        "quantized": import_graph(quantized) if present else skipped("quantized artifact absent")}

  report["qcom"] = skipped("phase not requested")
  if "compile" in phases:
    # A refusal is a measurement, not a crash: keep the phases that did produce numbers, and always
    # measure the archived artifact too — an int8 compile result means nothing without its baseline.
    report["qcom"] = {"archived": measured(lambda: compile_qcom(source, metadata, args.seed)),
                      "quantized": (measured(lambda: compile_qcom(quantized, metadata, args.seed)) if present
                                    else skipped("quantized artifact absent"))}

  report["numerics"] = skipped("phase not requested")
  if "numerics" in phases:
    report["numerics"] = (measured(lambda: numerics(source, quantized, metadata, args.seed)) if present
                          else skipped("quantized artifact absent"))

  (args.out / "report.json").write_text(dumps(report) + "\n")
  print(dumps(report))


if __name__ == "__main__":
  main()
