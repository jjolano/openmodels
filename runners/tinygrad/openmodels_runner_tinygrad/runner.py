from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from openmodels import ContractError, Manifest, Package, Recipe
from openmodels.client import verify_file
from openmodels.contracts import TARGET, dumps, loads, sha256, validate
from openmodels.profiles import STOCK_PROFILE, STOCK_SHA256, TINYGRAD_REVISION
from openmodels.runner import PreparedBuild

RUNNER = "openmodels/tinygrad-supercombo/v1"


def implementation_digest():
  """Bind local runner, Tinygrad source, Python and NumPy to the prepared build."""
  distribution = importlib.metadata.distribution("tinygrad")
  direct = loads(distribution.read_text("direct_url.json") or "{}")
  if direct.get("vcs_info", {}).get("commit_id") != TINYGRAD_REVISION:
    raise ContractError(f"install Tinygrad from the pinned Git revision {TINYGRAD_REVISION}")
  root = Path(importlib.util.find_spec("tinygrad").origin).parent
  digest = hashlib.sha256()
  sdk = Path(importlib.util.find_spec("openmodels").origin).parent
  for label, directory in (("runner", Path(__file__).parent), ("openmodels", sdk), ("tinygrad", root)):
    for path in sorted(directory.rglob("*.py")):
      digest.update(f"{label}/{path.relative_to(directory)}\0".encode())
      digest.update(path.read_bytes())
  digest.update(Path(importlib.util.find_spec("index.metadata").origin).read_bytes())
  digest.update(dumps({"python": sys.version, "numpy": importlib.metadata.version("numpy")}).encode())
  return digest.hexdigest()


def check_recipe(recipe, target):
  validate(target, TARGET)
  report = recipe.check(target)
  if recipe.profile.id != STOCK_PROFILE.id:
    raise ContractError("unsupported execution profile; archive profiles are not runnable")
  if recipe.data["members"]["supercombo"]["artifact"]["sha256"] != STOCK_SHA256:
    raise ContractError("this runner supports only the pinned stock artifact")
  config = recipe.data["configuration"]
  if report["findings"] or type(config.get("frame_skip")) is not int or config["frame_skip"] != 4:
    raise ContractError("execution configuration unresolved or unsupported")
  for key in ("LAT_SMOOTH_SECONDS", "LONG_SMOOTH_SECONDS"):
    if isinstance(config[key], bool) or not isinstance(config[key], (int, float)) or config[key] < 0:
      raise ContractError(f"invalid host constant: {key}")
  if target["backend"] != "QCOM" or target["hardware"] != "comma3x":
    raise ContractError("only comma3x QCOM execution is implemented; there is no CPU fallback")
  if target["runtime"] != f"tinygrad@{TINYGRAD_REVISION}":
    raise ContractError("target Tinygrad revision mismatch")
  if target["options"] != {"camera_width": 1928, "camera_height": 1208, "deadline_ms": 50}:
    raise ContractError("expected the pinned 1928x1208 camera profile and 50ms deadline")


class Runner:
  def __init__(self, build_root):
    self.root = Path(build_root).resolve()

  def prepare(self, package: Package, target, *, timeout=3600):
    check_recipe(package.recipe, target)
    package.verify()
    if not Path("/dev/kgsl-3d0").exists():
      raise ContractError("QCOM GPU unavailable: prepare this build on the target device")
    implementation = implementation_digest()
    self.root.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".build-", dir=self.root))
    try:
      target_path = staged / "target.json"
      target_path.write_text(dumps(target))
      # No commands, modules, environment settings or paths are taken from catalog metadata.
      with (staged / "compile.log").open("wb") as log:
        subprocess.run([sys.executable, "-m", "openmodels_runner_tinygrad.compile",
                        str(package.path.resolve()), str(staged), str(target_path)],
                       check=True, timeout=timeout, stdout=log, stderr=subprocess.STDOUT,
                       env={**os.environ, "DEV": "QCOM", "WARP_DEV": "QCOM"})
      artifact = staged / "model.pkl"
      if not artifact.is_file() or artifact.stat().st_size < 64 * 1024:
        raise ContractError("compiler did not produce a complete artifact")
      with artifact.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
      build = Manifest.create({"schema": 1, "type": "build", "recipe": package.recipe.id,
        "runner": RUNNER, "implementation": implementation, "target": target,
        "artifacts": {"model": {"sha256": digest, "size": artifact.stat().st_size, "format": "tinygrad-oob-pickle"}}})
      (staged / "build.json").write_text(build.raw)
      (staged / "recipe.json").write_text(package.recipe.manifest.raw)
      (staged / "profile.json").write_text(package.recipe.profile.raw)
      target_path.unlink()
      final = self.root / build.id
      if final.exists():
        verify_file(final / "model.pkl", build.data["artifacts"]["model"])
      else:
        staged.rename(final)
      return PreparedBuild(build, final, package.recipe)
    except (subprocess.SubprocessError, OSError) as exc:
      log_path = staged / "compile.log"
      tail = ""
      if log_path.exists():
        with log_path.open("rb") as handle:
          handle.seek(max(0, log_path.stat().st_size - 4096))
          tail = handle.read().decode(errors="replace")
      raise ContractError(f"target compilation failed: {exc}\n{tail}") from exc
    finally:
      shutil.rmtree(staged, ignore_errors=True)

  def open(self, build: PreparedBuild):
    data = build.manifest.data
    if data["type"] != "build" or data["runner"] != RUNNER or data["recipe"] != build.recipe.id:
      raise ContractError("build identity mismatch")
    check_recipe(build.recipe, data["target"])
    if build.path.resolve() != self.root / build.manifest.id:
      raise ContractError("only builds from this caller-owned local store may be opened")
    if (build.path / "build.json").read_bytes() != build.manifest.raw.encode():
      raise ContractError("build receipt changed")
    if implementation_digest() != data["implementation"]:
      raise ContractError("runner or runtime changed; prepare a new build")
    verify_file(build.path / "model.pkl", data["artifacts"]["model"])
    if not Path("/dev/kgsl-3d0").exists():
      raise ContractError("QCOM GPU unavailable")
    from tinygrad.device import Device
    if Device.DEFAULT != "QCOM":
      raise ContractError("initialize the consumer with DEV=QCOM before importing Tinygrad")
    from . import vendor  # Apply the pinned compressed-firmware lookup before device loading.
    from .serialization import load_oob
    from .session import DrivingSession
    # These are local executable build artifacts, never ONNX/catalog metadata.
    with (build.path / "model.pkl").open("rb") as handle:
      executable = load_oob(handle)
    return DrivingSession(executable, build.manifest.id)
