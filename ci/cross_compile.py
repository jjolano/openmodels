"""Experimental stock a630 build on Linux x86-64; never creates a runner receipt."""
import argparse
import functools
import hashlib
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile

from openmodels import Catalog, ContractError, ModelStore
from openmodels.contracts import dumps
from openmodels.profiles import STOCK_SHA256, TINYGRAD_REVISION

TOOLCHAIN_URL = "https://git.tinygrad.win/sirhcm/images/releases/download/v2/qcomcl.tar.gz"
TOOLCHAIN_SHA256 = "a9e8aa32d5e4297c7b8408d29a5545384b0ffef25ef91e0e32c401be9d4be6af"


def install_bridge():
  """Process-local capture shim. Original SINKs stay on the host, outside the pickle."""
  from tinygrad.device import Compiled, Device
  from tinygrad.renderer.cstyle import QCOMCLRenderer
  from tinygrad.codegen import to_program
  from tinygrad.engine import realize
  from tinygrad.uop.ops import Ops
  from tinygrad.helpers import fetch
  from tinygrad.runtime.support import compiler_qcom

  def fetch_toolchain(url):
    if url != TOOLCHAIN_URL:
      raise ContractError("unexpected QCOM toolchain URL")
    return fetch(url, sha256=TOOLCHAIN_SHA256)
  compiler_qcom.fetch = fetch_toolchain
  cpu = Device["CPU"]
  original_class = Device.get_class

  class HostQCOM(Compiled):
    def __init__(self, device):
      super().__init__(device, cpu.allocator, [QCOMCLRenderer], None, arch="a630")

    def synchronize(self):
      cpu.synchronize()

  Device.get_class = lambda name: HostQCOM if name.split(":")[0] == "QCOM" else original_class(name)
  original_runtime = realize.get_runtime
  original_sinks = {}

  def compile_program(ast, renderer):
    program = to_program(ast, renderer)
    if renderer.target.device == "QCOM" and ast.op is Ops.SINK:
      original_sinks[program.key] = ast
    return program
  realize.to_program = compile_program

  @functools.cache
  def host_runtime(ast):
    # Like Tinygrad's exec_validate, compile the original semantic kernel for CPU.
    # PROGRAM.src[0] is already GPU-lowered and cannot be retargeted this way.
    host = to_program(original_sinks[ast.key], cpu.renderer)
    runtime = original_runtime("CPU", host)
    indexes = [ast.arg.globals.index(slot) for slot in host.arg.globals]

    def execute(*buffers, vals=(), wait=False, timeout=None, **unused):
      variables = {v.expr: value for v, value in zip(ast.arg.vars, vals, strict=True)}
      global_size, local_size = host.arg.launch_dims(variables)
      return runtime(*[buffers[i] for i in indexes], vals=host.arg.vals(variables), global_size=global_size,
                     local_size=local_size, wait=wait, timeout=timeout)
    return execute

  def get_runtime(device, ast, cache=True):
    return host_runtime(ast) if device.split(":")[0] == "QCOM" else original_runtime(device, ast, cache)
  realize.get_runtime = get_runtime


def check_bridge():
  """Independent weighted reduction oracle, including serialized weight replay."""
  import io
  import numpy as np
  from tinygrad import Tensor, TinyJit
  from openmodels_runner_tinygrad.serialization import dump_oob, load_oob
  rng = np.random.default_rng(123)
  matrix = rng.normal(size=(32, 32)).astype(np.float32)
  weights = Tensor(matrix, device="QCOM").realize()

  @TinyJit
  def dense(x):
    return (x @ weights).realize()

  for _ in range(3):
    x = rng.normal(size=(32, 32)).astype(np.float32)
    np.testing.assert_allclose(dense(Tensor(x, device="QCOM")).numpy(), x @ matrix, rtol=1e-4, atol=1e-4)
  stream = io.BytesIO()
  dump_oob(dense, stream)
  stream.seek(0)
  replay = load_oob(stream)
  x = rng.normal(size=(32, 32)).astype(np.float32)
  np.testing.assert_allclose(replay(Tensor(x, device="QCOM")).numpy(), x @ matrix, rtol=1e-4, atol=1e-4)


def inspect_artifact(path):
  from tinygrad.uop.ops import Ops
  from openmodels_runner_tinygrad.serialization import load_oob
  # Only the artifact just created by this process is deserialized.
  with path.open("rb") as handle:
    result = load_oob(handle)
  nodes = {u for jit in (result["run_policy"], result[(1928, 1208)]) for u in jit.captured._linear.toposort()}
  programs = [u for u in nodes if u.op is Ops.PROGRAM]
  if not programs or any(p.arg.target.device != "QCOM" or p.arg.target.arch != "a630" or not p.to_elf().lib for p in programs):
    raise ContractError("artifact does not contain exclusively compiled a630 programs")
  if any(u.op is Ops.BUFFER and u.device not in (None, "QCOM", "NPY") for u in nodes):
    raise ContractError("host buffer escaped into target artifact")
  return {"programs": len(programs), "kernel_bytes": sum(len(p.to_elf().lib) for p in programs)}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--catalog", required=True)
  parser.add_argument("--store", type=Path, required=True)
  parser.add_argument("--out", type=Path, required=True)
  args = parser.parse_args()
  if platform.system() != "Linux" or platform.machine() != "x86_64" or not shutil.which("qemu-aarch64-static"):
    parser.error("requires Linux x86-64 and qemu-aarch64-static")
  if args.out.exists():
    parser.error("output directory must not already exist")
  # Set these before importing Tinygrad; no timing-based tuning or image allocation emulation.
  os.environ.update(DEV="QCOM;CPU:LLVM", WARP_DEV="QCOM", BEAM="0", IMAGE="0", JIT_BATCH_SIZE="0", PYTHONDONTWRITEBYTECODE="1")
  from openmodels_runner_tinygrad.runner import implementation_digest
  implementation = implementation_digest()  # Enforces the installed Tinygrad Git pin.
  import tinygrad
  # The ARM Python in the upstream compiler sysroot must import this exact Tinygrad.
  os.environ["PYTHONPATH"] = str(Path(tinygrad.__file__).parent.parent)
  catalog = Catalog.load(args.catalog)
  package = ModelStore(args.store, catalog).fetch(catalog.resolve("Stock supercombo (555f48c5)"))
  target = {"backend": "QCOM", "hardware": "comma3x", "os": "unvalidated",
            "runtime": f"tinygrad@{TINYGRAD_REVISION}",
            "options": {"camera_width": 1928, "camera_height": 1208, "deadline_ms": 50}}
  install_bridge()
  args.out.parent.mkdir(parents=True, exist_ok=True)
  from openmodels_runner_tinygrad.compile import compile_package
  from tinygrad.device import Device
  compiler = Device["QCOM"].compiler
  try:
    check_bridge()
    with tempfile.TemporaryDirectory(prefix=".cross-", dir=args.out.parent) as tmp:
      staged = Path(tmp) / "result"
      staged.mkdir()
      (staged / "target.json").write_text(dumps(target))
      compile_package(package.path, staged, staged / "target.json")
      artifact = staged / "model.pkl"
      report = {"experimental": True, "gpu_validated": False, "recipe": package.recipe.id,
                "source_sha256": STOCK_SHA256, "implementation": implementation, "target": target,
                "cross_compiler_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "host_target": str(Device["CPU"].renderer.target),
                "toolchain_sha256": TOOLCHAIN_SHA256, "python": sys.version,
                "checks": ["weighted-matmul-oracle", "policy-and-warp-seeded-replay", "pickle-round-trip", "target-program-inspection"],
                **inspect_artifact(artifact), "size": artifact.stat().st_size}
      with artifact.open("rb") as handle:
        report["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
      (staged / "report.json").write_text(dumps(report) + "\n")
      staged.rename(args.out)
      print(dumps(report))
  finally:
    # Stop ARM Python before its sysroot TemporaryDirectory is cleaned up.
    process = compiler.compiler_process
    process.kill()
    process.wait()


if __name__ == "__main__":
  main()
