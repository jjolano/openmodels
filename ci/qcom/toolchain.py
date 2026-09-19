"""Pinned QCOM toolchain capture: compiles a630 programs on Linux x86-64 via qemu + LLVM."""
from functools import cache
import hashlib
from pathlib import Path

from openmodels import ContractError

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

  @cache
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
  from .serialization import dump_oob, load_oob
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
  from .serialization import load_oob
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
