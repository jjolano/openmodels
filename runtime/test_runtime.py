#!/usr/bin/env python3
"""Self-check for the integration library. Run: python3 runtime/test_runtime.py

Covers the two things a fork would get wrong on its own: which compiler can accept a given
bundle, and whether a half-finished download can be mistaken for an installed model.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.manager import Catalog, ModelStore  # noqa: E402
from runtime.plan import (  # noqa: E402
  MULTI_ERA, UPSTREAM, PlanError, classify, detect_input_keys, plan_bundle,
)


def test_classify_routes_to_a_compiler_that_accepts_it():
  assert classify({"vision", "on_policy"}) == ("vision_policy", UPSTREAM)
  # Upstream has no --supercombo-onnx and no off-policy input.
  assert classify({"supercombo"}) == ("supercombo", MULTI_ERA)
  assert classify({"vision", "on_policy", "off_policy"}) == ("vision_multi_policy", MULTI_ERA)
  try:
    classify({"dmonitoring"})
    raise AssertionError("dmonitoring is not a driving model and must not plan")
  except PlanError:
    pass


def test_detects_input_keys_across_six_years_of_renames():
  modern = detect_input_keys({"img": [], "big_img": [], "desire_pulse": [],
                              "traffic_convention": [], "features_buffer": [], "action_t": []})
  assert modern["img"] == "img" and modern["big_img"] == "big_img"
  assert modern["desire"] == "desire_pulse"

  legacy = detect_input_keys({"input_imgs": [], "big_input_imgs": [], "desire": [],
                              "traffic_convention": [], "features_buffer": []})
  assert legacy["img"] == "input_imgs", legacy
  assert legacy["big_img"] == "big_input_imgs", legacy
  assert legacy["desire"] == "desire", legacy
  assert legacy["action_t"] is None       # legacy models have no action input


def _bundle(roles, **extra):
  return {"bundle_id": "test0001",
          "files": [{"role": r, "filename": f"{r}.onnx", "oid": "0" * 64, "size": 1} for r in roles],
          **extra}


def test_plan_maps_roles_to_flags_not_filenames():
  with tempfile.TemporaryDirectory() as tmp:
    files = {r: Path(tmp) / f"{r}.onnx" for r in ("vision", "on_policy")}
    for p in files.values():
      p.write_bytes(b"")                  # unparseable on purpose: plan must still be produced
    plan = plan_bundle(_bundle(["vision", "on_policy"], frame_skip=4), files)

  assert plan.model_type == "vision_policy" and plan.compiler == UPSTREAM
  assert plan.onnx_flags["--vision-onnx"].name == "vision.onnx"
  assert plan.onnx_flags["--on-policy-onnx"].name == "on_policy.onnx"
  argv = plan.command("compile_modeld.py", "out.pkl", "512x256", ["1928x1208"])
  assert "--model-type" not in argv, "upstream compiler has no --model-type"
  assert argv[argv.index("--frame-skip") + 1] == "4"


def test_supercombo_plan_targets_the_multi_era_compiler():
  with tempfile.TemporaryDirectory() as tmp:
    files = {"supercombo": Path(tmp) / "sc.onnx"}
    files["supercombo"].write_bytes(b"")
    plan = plan_bundle(_bundle(["supercombo"], frame_skip=4), files)
  argv = plan.command("compile_modeld.py", "out.pkl", "512x256", ["1928x1208"])
  assert argv[argv.index("--model-type") + 1] == "supercombo"
  assert any("multi-era" in w for w in plan.warnings), plan.warnings


def test_frame_skip_disagreement_is_surfaced_not_resolved():
  with tempfile.TemporaryDirectory() as tmp:
    files = {r: Path(tmp) / f"{r}.onnx" for r in ("vision", "on_policy")}
    for p in files.values():
      p.write_bytes(b"")
    plan = plan_bundle(_bundle(["vision", "on_policy"], frame_skip=1), files, host_frame_skip=4)
  assert plan.frame_skip == 4, "the host's value must win"
  assert any("mismatch" in w for w in plan.warnings), plan.warnings


def test_missing_constants_warn_and_are_never_defaulted():
  with tempfile.TemporaryDirectory() as tmp:
    files = {r: Path(tmp) / f"{r}.onnx" for r in ("vision", "on_policy")}
    for p in files.values():
      p.write_bytes(b"")
    plan = plan_bundle(
      _bundle(["vision", "on_policy"], host_constants={},
              host_constants_missing=["LAT_SMOOTH_SECONDS"]), files, host_frame_skip=4)
  assert plan.host_constants == {}
  assert any("absent, not zero" in w for w in plan.warnings), plan.warnings


def test_plan_refuses_a_bundle_whose_files_are_absent():
  try:
    plan_bundle(_bundle(["vision", "on_policy"]), {"vision": Path("/nope/v.onnx")})
    raise AssertionError("planning must fail when a role has no local file")
  except PlanError as exc:
    assert "on_policy" in str(exc)


def test_store_activation_and_constants():
  with tempfile.TemporaryDirectory() as tmp:
    store = ModelStore(Path(tmp) / "models")
    prov = {"files": [{"role": "vision", "filename": "v.onnx", "oid": "a" * 64, "size": 1}],
            "host_constants": {"LAT_SMOOTH_SECONDS": 0.0}, "frame_skip": 4}
    assert store.active() is None and store.active_constants() == {}

    try:
      store.set_active("ghost")
      raise AssertionError("must not activate a model that is not installed")
    except FileNotFoundError:
      pass

    store.record("b1", prov)
    (store.path_for("b1")).mkdir(parents=True, exist_ok=True)
    (store.path_for("b1") / "v.onnx").write_bytes(b"x")
    assert store.is_installed("b1")
    store.set_active("b1")
    assert store.active() == "b1"
    assert store.active_constants() == {"LAT_SMOOTH_SECONDS": 0.0}

    store.remove("b1")
    assert store.active() is None and not store.is_installed("b1")


def test_missing_file_means_not_installed():
  """A recorded bundle whose files vanished must not read as installed."""
  with tempfile.TemporaryDirectory() as tmp:
    store = ModelStore(Path(tmp) / "models")
    store.record("b1", {"files": [{"role": "vision", "filename": "v.onnx",
                                   "oid": "a" * 64, "size": 1}]})
    assert not store.is_installed("b1"), "no files on disk, so not installed"


def test_catalog_filters_and_groups_real_index():
  index = Path(__file__).resolve().parent.parent / "data" / "index.json"
  if not index.exists():
    print("  (skipped catalog test: data/index.json not built)")
    return
  cat = Catalog(json.loads(index.read_text()), "file", "http://localhost")
  merged = cat.list(kind="driving")
  withdrawn = cat.list(kind="driving", allow_statuses={"reverted", "pr_only"})
  assert merged and withdrawn
  assert not (set(b["bundle_id"] for b in merged) & set(b["bundle_id"] for b in withdrawn)), \
    "withdrawn models must never appear in the default listing"
  years = cat.group_by_year(kind="driving")
  assert list(years) == sorted(years, reverse=True), "newest year first"
  sc = cat.list(kind="driving", family="supercombo")
  assert all(b["family"] == "supercombo" for b in sc)


if __name__ == "__main__":
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  for test in tests:
    test()
    print(f"  ok  {test.__name__}")
  print(f"\n{len(tests)} passed")
