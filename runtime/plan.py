"""Work out how to compile a model bundle, without compiling it.

The hard part of turning an archived bundle into a runnable artifact is not the compile — it is
knowing *how to invoke* the compiler for a model that may be six years old: which model type it
is, which file feeds which flag, and what its inputs are called this era. That decision is pure
logic and is what this module produces.

Deliberately does not touch the control path. It emits a plan; the caller runs it with their own
compiler, keeps their own modeld, and owns qualification.

Input-key detection follows sunnypilot's approach (MIT, sunnypilot/modeld_v2/compile_modeld.py):
match by prefix and substring rather than exact names, because openpilot renamed `input_imgs` to
`img` and `desire` to `desire_pulse` between eras.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from index import metadata

# Which compilers accept which shapes. Upstream's compile_modeld.py requires --vision-onnx and
# --on-policy-onnx and offers nothing else; sunnypilot's takes a --model-type and covers all
# three. Getting this wrong wastes a multi-minute on-device compile.
UPSTREAM = "openpilot"
MULTI_ERA = "sunnypilot"

MODEL_TYPES = ("vision_policy", "supercombo", "vision_multi_policy")


class PlanError(Exception):
  """The bundle cannot be compiled as given."""


@dataclass(frozen=True)
class Capabilities:
  """What a fork can actually build and run.

  Only mechanical facts belong here: which compilers are installed, whether the device has the
  USB GPU the `big_` family targets, and the build's own frame skip. These are checkable, unlike
  whether a model's outputs mean what your parser thinks they mean.
  """
  compilers: frozenset[str] = frozenset({UPSTREAM})
  usbgpu: bool = False
  frame_skip: int | None = None

  @classmethod
  def detect(cls, openpilot_root: Path | None = None) -> "Capabilities":
    """Best-effort local detection. Explicit construction is preferable in a fork."""
    compilers = set()
    root = Path(openpilot_root or ".")
    if (root / "selfdrive/modeld/compile_modeld.py").exists():
      compilers.add(UPSTREAM)
    if (root / "sunnypilot/modeld_v2/compile_modeld.py").exists():
      compilers.add(MULTI_ERA)
    usbgpu = False
    try:                                      # mirrors openpilot's usbgpu_present()
      usbgpu = any((d / "idVendor").exists() for d in Path("/sys/bus/usb/devices").glob("*"))
    except OSError:
      pass
    return cls(frozenset(compilers or {UPSTREAM}), usbgpu)


@dataclass(frozen=True)
class Blocker:
  """Why a bundle will not run here — phrased as the support that is missing.

  `code` is stable and aggregatable on purpose: a blocker is really a feature request, and a
  maintainer should be able to ask "what would adding X unlock?" rather than treating each
  unsupported model as a dead end.
  """
  code: str
  detail: str


@dataclass
class Compatibility:
  """Whether a bundle is *mechanically* runnable here, and why not.

  `runnable` never means safe or correct — only that this fork can build it and the hardware
  matches. Semantic compatibility is not decidable from the data and is deliberately absent.
  """
  bundle_id: str
  runnable: bool
  blockers: list[Blocker] = field(default_factory=list)
  cautions: list[str] = field(default_factory=list)


def check_compatibility(bundle: dict[str, Any], caps: Capabilities,
                        suspect_oids: frozenset[str] = frozenset()) -> Compatibility:
  """Mechanical screen only. Blockers are facts; cautions need a human."""
  blockers: list[Blocker] = []
  cautions: list[str] = []
  roles = {f["role"] for f in bundle.get("files", [])}

  try:
    model_type, needed = classify(roles)
  except PlanError as exc:
    return Compatibility(bundle["bundle_id"], False,
                         [Blocker("unknown_architecture", str(exc))])

  if needed not in caps.compilers:
    blockers.append(Blocker(
      f"needs_compiler:{needed}",
      f"{model_type} needs the {needed} compiler; you have {', '.join(sorted(caps.compilers))}",
    ))
  if bundle.get("variant") == "big" and not caps.usbgpu:
    blockers.append(Blocker(
      "needs_usbgpu", "the big_ family targets a USB GPU (DEV=USB+AMD); none detected"))
  if any(f["oid"] in suspect_oids for f in bundle.get("files", [])):
    blockers.append(Blocker(
      "suspect_file", "contains a file flagged suspect (upstream conflict debris, not a model)"))

  status = bundle.get("status")
  if status and status != "merged":
    cautions.append(
      f"withdrawn upstream (status: {status}) — comma pulled this back or never merged it"
    )
  if missing := bundle.get("host_constants_missing"):
    cautions.append(f"host constants not recorded upstream ({', '.join(missing)}); "
                    f"you must determine them yourself")
  recorded = bundle.get("frame_skip")
  if caps.frame_skip is not None and recorded is not None and caps.frame_skip != recorded:
    cautions.append(f"ran upstream with frame_skip={recorded}, your build uses {caps.frame_skip}")

  return Compatibility(bundle["bundle_id"], not blockers, blockers, cautions)


@dataclass
class CompilePlan:
  bundle_id: str
  model_type: str
  compiler: str                                  # UPSTREAM or MULTI_ERA
  onnx_flags: dict[str, Path]                    # --vision-onnx etc -> file
  input_keys: dict[str, str | None]              # logical name -> this model's actual key
  frame_skip: int | None
  host_constants: dict[str, Any] = field(default_factory=dict)
  warnings: list[str] = field(default_factory=list)

  def command(self, compiler_path: str, output: str, model_size: str,
              camera_resolutions: list[str]) -> list[str]:
    """The exact argv to run.

    `model_size` and `camera_resolutions` are *host* properties (the fork's cameras and model
    input size), not model properties, so the caller supplies them — the same reason frame_skip
    is reported rather than imposed.
    """
    if self.frame_skip is None:
      raise PlanError("frame_skip unknown; supply it from your own ModelConstants")
    argv = ["python3", compiler_path]
    if self.compiler == MULTI_ERA:
      argv += ["--model-type", self.model_type]
    for flag, path in sorted(self.onnx_flags.items()):
      argv += [flag, str(path)]
    argv += ["--model-size", model_size, "--camera-resolutions", *camera_resolutions,
             "--frame-skip", str(self.frame_skip), "--output", output]
    return argv

  def to_dict(self) -> dict[str, Any]:
    return {
      "bundle_id": self.bundle_id,
      "model_type": self.model_type,
      "compiler": self.compiler,
      "onnx_flags": {k: str(v) for k, v in self.onnx_flags.items()},
      "input_keys": self.input_keys,
      "frame_skip": self.frame_skip,
      "host_constants": self.host_constants,
      "warnings": self.warnings,
    }


def detect_input_keys(input_shapes: dict[str, Any]) -> dict[str, str | None]:
  """Map logical inputs to this model's actual key names.

  Six years of renames: `input_imgs`/`big_input_imgs` became `img`/`big_img`, and `desire`
  became `desire_pulse`. Substring matching absorbs both.
  """
  img_keys = sorted(k for k in input_shapes if "img" in k)
  return {
    "img": next((k for k in img_keys if "big" not in k), None),
    "big_img": next((k for k in img_keys if "big" in k), None),
    "desire": next((k for k in input_shapes if k.startswith("desire")), None),
    "traffic_convention": next((k for k in input_shapes if "traffic" in k), None),
    "features_buffer": next((k for k in input_shapes if "features" in k or "feat" in k), None),
    "action_t": next((k for k in input_shapes if k.startswith("action")), None),
  }


def classify(roles: set[str]) -> tuple[str, str]:
  """(model_type, required compiler) from a bundle's roles."""
  if "supercombo" in roles:
    # Self-contained graph. Upstream's compiler has no --supercombo-onnx.
    return "supercombo", MULTI_ERA
  if "off_policy" in roles:
    return "vision_multi_policy", MULTI_ERA
  if {"vision", "on_policy"} <= roles:
    return "vision_policy", UPSTREAM
  raise PlanError(f"no compiler handles this role set: {sorted(roles)}")


# role -> compiler flag. Mapping is by role, never by filename: commit 249cafe renamed
# driving_policy.onnx to driving_on_policy.onnx with identical content, so an older bundle's
# on_policy role still arrives under the older name.
ROLE_FLAGS = {
  "vision": "--vision-onnx",
  "on_policy": "--on-policy-onnx",
  "off_policy": "--off-policy-onnx",
  "supercombo": "--supercombo-onnx",
}


def plan_bundle(bundle: dict[str, Any], files: dict[str, Path],
                host_frame_skip: int | None = None) -> CompilePlan:
  """Build a compile plan from a provenance record and the downloaded files.

  `files` maps role -> local path. `host_frame_skip` is the fork's own value; when given it
  wins, and a disagreement with the model's recorded value is surfaced as a warning rather
  than silently resolved.
  """
  roles = {f["role"] for f in bundle["files"]}
  missing = roles - set(files)
  if missing:
    raise PlanError(f"missing local files for roles: {sorted(missing)}")

  model_type, compiler = classify(roles)
  warnings: list[str] = []

  onnx_flags: dict[str, Path] = {}
  for role in roles:
    flag = ROLE_FLAGS.get(role)
    if flag is None:
      warnings.append(f"role {role!r} has no compiler flag; ignored")
      continue
    onnx_flags[flag] = files[role]

  # Read the keys off the model that actually drives the graph.
  primary = files.get("vision") or files.get("supercombo") or next(iter(files.values()))
  try:
    input_keys = detect_input_keys(metadata.parse(str(primary))["input_shapes"])
  except Exception as exc:
    input_keys = {}
    warnings.append(f"could not read input keys from {primary.name}: {exc}")

  recorded = bundle.get("frame_skip")
  frame_skip = host_frame_skip if host_frame_skip is not None else recorded
  if host_frame_skip is not None and recorded is not None and host_frame_skip != recorded:
    warnings.append(
      f"frame_skip mismatch: your build uses {host_frame_skip}, this model ran upstream with "
      f"{recorded}. frame_skip is a host property, but a difference means the model expects a "
      f"different temporal cadence — investigate before driving."
    )
  if frame_skip is None:
    warnings.append("frame_skip unknown: this era predates the constants it is derived from; "
                    "supply your own from ModelConstants")

  constants = bundle.get("host_constants", {})
  if absent := bundle.get("host_constants_missing"):
    warnings.append(
      f"host constants not recorded upstream: {', '.join(absent)}. They are absent, not zero — "
      f"do not substitute defaults; a wrong smoothing constant changes steering silently."
    )
  if compiler == MULTI_ERA:
    warnings.append(
      f"model_type {model_type!r} needs a multi-era compiler (sunnypilot's compile_modeld.py); "
      f"openpilot's accepts only vision+on_policy."
    )

  return CompilePlan(
    bundle_id=bundle["bundle_id"], model_type=model_type, compiler=compiler,
    onnx_flags=onnx_flags, input_keys=input_keys, frame_skip=frame_skip,
    host_constants=constants, warnings=warnings,
  )


def main() -> int:
  import argparse
  parser = argparse.ArgumentParser(description="Plan a bundle compile (does not compile)")
  parser.add_argument("--provenance", required=True, type=Path,
                      help="JSON from /v1/models/{id}/provenance")
  parser.add_argument("--dir", required=True, type=Path, help="directory with the ONNX files")
  parser.add_argument("--frame-skip", type=int, help="your build's value")
  args = parser.parse_args()

  bundle = json.loads(args.provenance.read_text())
  files = {f["role"]: args.dir / f["filename"] for f in bundle["files"]}
  print(json.dumps(plan_bundle(bundle, files, args.frame_skip).to_dict(), indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
