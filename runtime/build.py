"""Run a compile on the device, and only activate a model that survived it.

Compilation belongs on the target: tinygrad's QCOM backend opens /dev/kgsl-3d0 directly, so a
device-usable artifact cannot be produced anywhere else. That is not a limitation to work
around — it means the device is also the right place to *qualify* the artifact.

What makes this safe to automate is that compilation is **fail-loud**. A compile either emits a
pickle or it does not; the pickle either loads or it does not. Nothing here can silently drive
wrong, which is precisely why this package will orchestrate a compile but never run inference
or interpret outputs (see AGENTS.md).

The rails, in order: compile into staging → smoke-test the artifact → move it into place →
activate. Any failure leaves the previously active model untouched.
"""

from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from runtime.plan import CompilePlan

# A compiled pickle that is implausibly small never contains a model; catching that here beats
# discovering it when modeld fails to start onroad.
MIN_ARTIFACT_BYTES = 64 * 1024


class BuildError(Exception):
  """The compile failed, or produced something that did not survive inspection."""


@dataclass
class BuildResult:
  bundle_id: str
  artifact: Path
  seconds: float
  size_bytes: int
  compiler_argv: list[str]
  checks: list[str] = field(default_factory=list)
  stdout_tail: str = ""


def smoke_test(artifact: Path) -> list[str]:
  """Cheap structural checks on a freshly compiled artifact.

  Deliberately does not run inference: executing a driving model to 'check' it would mean
  interpreting its outputs, which is the boundary this package does not cross. These checks
  answer "did the compiler produce something loadable", not "is this model good".
  """
  checks: list[str] = []
  if not artifact.exists():
    raise BuildError(f"compiler reported success but produced no {artifact.name}")

  size = artifact.stat().st_size
  if size < MIN_ARTIFACT_BYTES:
    raise BuildError(f"artifact is only {size} bytes; the compile did not produce a model")
  checks.append(f"size {size/2**20:.1f} MB")

  try:
    with open(artifact, "rb") as handle:
      loaded = pickle.load(handle)
  except Exception as exc:
    raise BuildError(f"artifact does not unpickle: {type(exc).__name__}: {exc}") from exc
  checks.append("unpickles")

  # openpilot's compiler emits a dict carrying the JIT'd runners plus a metadata entry; a
  # pickle without it will fail later inside modeld, where the failure is far less legible.
  if isinstance(loaded, dict):
    if "metadata" not in loaded:
      raise BuildError(f"artifact has no 'metadata' key (got {sorted(loaded)[:6]})")
    checks.append("carries metadata")
  return checks


def build(plan: CompilePlan, compiler: Path, output_dir: Path, *,
          model_size: str, camera_resolutions: list[str],
          timeout: int = 3600, env: dict[str, str] | None = None,
          on_output: Callable[[str], None] | None = None) -> BuildResult:
  """Compile into staging, verify, then move into place. Never overwrites on failure.

  `timeout` is generous by default: an on-device compile is a multi-minute operation, and
  killing it early leaves the user with nothing while looking like a crash.
  """
  output_dir = Path(output_dir)
  output_dir.mkdir(parents=True, exist_ok=True)
  staging = output_dir / f".build-{plan.bundle_id}"
  shutil.rmtree(staging, ignore_errors=True)
  staging.mkdir(parents=True)

  artifact_name = f"{plan.bundle_id}_tinygrad.pkl"
  staged_artifact = staging / artifact_name
  argv = plan.command(str(compiler), str(staged_artifact), model_size, camera_resolutions)

  started = time.monotonic()
  lines: list[str] = []
  try:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env={**os.environ, **(env or {})})
    assert process.stdout is not None
    for line in process.stdout:
      lines.append(line.rstrip())
      if on_output:
        on_output(line.rstrip())
    code = process.wait(timeout=timeout)
    if code != 0:
      raise BuildError(f"compiler exited {code}\n" + "\n".join(lines[-15:]))

    checks = smoke_test(staged_artifact)

    final = output_dir / artifact_name
    tmp = output_dir / f".{artifact_name}.new"
    shutil.copy2(staged_artifact, tmp)
    tmp.replace(final)                       # atomic: modeld never sees a partial pickle
  except subprocess.TimeoutExpired as exc:
    process.kill()
    raise BuildError(f"compile exceeded {timeout}s") from exc
  finally:
    shutil.rmtree(staging, ignore_errors=True)

  return BuildResult(
    bundle_id=plan.bundle_id, artifact=final, seconds=time.monotonic() - started,
    size_bytes=final.stat().st_size, compiler_argv=argv, checks=checks,
    stdout_tail="\n".join(lines[-15:]),
  )


def build_and_activate(plan: CompilePlan, compiler: Path, store: Any, *,
                       model_size: str, camera_resolutions: list[str],
                       **kwargs) -> BuildResult:
  """Compile, and activate only if it survived.

  The previously active model is restored on any failure, so a bad compile costs the user a
  wait rather than a working car.
  """
  previous = store.active()
  try:
    result = build(plan, compiler, store.path_for(plan.bundle_id),
                   model_size=model_size, camera_resolutions=camera_resolutions, **kwargs)
    store.set_active(plan.bundle_id)
    return result
  except Exception:
    if store.active() != previous:
      store.set_active(previous)
    raise
