#!/usr/bin/env python3
"""Self-check for the integration library. Run: python3 runtime/test_runtime.py

Covers the two things a fork would get wrong on its own: which compiler can accept a given
bundle, and whether a half-finished download can be mistaken for an installed model.
"""

import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.build import BuildError, build, build_and_activate, smoke_test  # noqa: E402
from runtime import manager  # noqa: E402
from runtime.manager import ALL_STATUSES, MERGED_ONLY, Catalog, ModelStore  # noqa: E402
from runtime.plan import (  # noqa: E402
  MULTI_ERA, UPSTREAM, Capabilities, PlanError, check_compatibility, classify,
  detect_input_keys, plan_bundle,
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
    oid = hashlib.sha256(b"x").hexdigest()
    prov = {"files": [{"role": "vision", "filename": "v.onnx", "oid": oid, "size": 1}],
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

    (store.path_for("b1") / "v.onnx").write_bytes(b"corrupt")
    assert not store.is_installed("b1"), "installed files must be re-hashed before activation"
    (store.path_for("b1") / "v.onnx").write_bytes(b"x")

    store.remove("b1")
    assert store.active() is None and not store.is_installed("b1")


def test_store_preserves_composed_provenance_without_collapsing_it():
  with tempfile.TemporaryDirectory() as tmp:
    store = ModelStore(Path(tmp))
    provenance = {
      "files": [], "source": "composed", "attested": False,
      "host_constants_by_role": {"vision": {}, "on_policy": {"MODEL_FREQ": 20}},
      "host_constants_missing_by_role": {"vision": ["MODEL_FREQ"], "on_policy": []},
      "frame_skip_by_role": {"vision": None, "on_policy": 1},
      "cautions": ["host configuration unresolved"],
    }
    store.record("composed", provenance)
    saved = store.installed()["composed"]
    assert not store.is_installed("composed")
  assert saved["attested"] is False
  assert saved["host_constants_by_role"] == provenance["host_constants_by_role"]
  assert saved["cautions"] == provenance["cautions"]


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
  everything = cat.list(kind="driving")
  merged = cat.list(kind="driving", allow_statuses=MERGED_ONLY)
  assert len(everything) > len(merged), "default listing must include withdrawn models"
  assert all("statuses" in b for b in everything), "every entry must carry occurrence statuses"
  assert all("merged" in b["statuses"] for b in merged)
  years = cat.group_by_year(kind="driving")
  assert list(years) == sorted(years, reverse=True), "newest year first"
  sc = cat.list(kind="driving", family="supercombo")
  assert all(b["family"] == "supercombo" for b in sc)




def test_incompatible_models_are_flagged_not_hidden():
  """A fork maintainer needs to see what it cannot run, and why."""
  index = Path(__file__).resolve().parent.parent / "data" / "index.json"
  if not index.exists():
    print("  (skipped: data/index.json not built)")
    return
  cat = Catalog(json.loads(index.read_text()), "file", "http://localhost")
  caps = Capabilities(compilers=frozenset({UPSTREAM}), usbgpu=False)

  listed = cat.list(kind="driving", capabilities=caps)
  runnable = cat.list(kind="driving", capabilities=caps, only_runnable=True)
  assert len(listed) > len(runnable), "default must list models it cannot run"
  assert all("compatibility" in b for b in listed)

  blocked = [b for b in listed if not b["compatibility"]["runnable"]]
  assert blocked, "an upstream-only fork cannot run the supercombo bundles"
  assert all(b["compatibility"]["blockers"] for b in blocked), "a blocker must say why"


def test_support_gaps_aggregate_into_a_roadmap():
  index = Path(__file__).resolve().parent.parent / "data" / "index.json"
  if not index.exists():
    return
  cat = Catalog(json.loads(index.read_text()), "file", "http://localhost")
  gaps = cat.support_gaps(Capabilities(compilers=frozenset({UPSTREAM})), kind="driving")
  assert gaps and gaps[0][1] > 0
  codes = [g[0] for g in gaps]
  assert any(c.startswith("needs_compiler:") for c in codes), codes
  assert gaps == sorted(gaps, key=lambda g: g[1], reverse=True), "largest win first"


def test_reverted_occurrence_is_a_caution_not_a_blocker():
  caps = Capabilities(compilers=frozenset({UPSTREAM, MULTI_ERA}))
  bundle = _bundle(["vision", "on_policy"], variant="standard", statuses=["reverted"])
  verdict = check_compatibility(bundle, caps)
  assert verdict.runnable, "a reverted model is still mechanically runnable"
  assert any("reverted occurrence" in c for c in verdict.cautions), verdict.cautions


def test_api_summary_roles_are_enough_for_capability_checks():
  bundle = {"bundle_id": "summary", "kind": "driving", "variant": "standard",
            "family": "split", "roles": ["vision", "on_policy"], "status": "merged",
            "introduced_by": {"date": "2026-01-01T00:00:00Z"}}
  cat = Catalog({"bundles": [bundle]}, "api", "https://example.test")
  listed = cat.list(capabilities=Capabilities(compilers=frozenset({UPSTREAM})))
  assert listed[0]["compatibility"]["runnable"] is True, listed


def test_catalog_uses_recorded_release_without_a_live_api():
  oid = "a" * 64
  cat = Catalog({"release_repo": "owner/repo",
                 "files": [{"oid": oid, "release": "blobs-0003"}]}, "mirror", "")
  assert cat.download_url(oid) == \
    f"https://github.com/owner/repo/releases/download/blobs-0003/{oid}.onnx"


def test_catalog_recovers_from_a_torn_cache_atomically():
  with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp) / "catalog.json"
    cache.write_text("{torn")
    old_get = manager._get
    manager._get = lambda *_: json.dumps({"bundles": []}).encode()
    try:
      catalog = Catalog.load("https://api.example", cache=cache)
    finally:
      manager._get = old_get
    assert catalog.source == "api" and json.loads(cache.read_text()) == {"bundles": []}
    assert not cache.with_suffix(".json.tmp").exists()



# --- build rails: testable without a device, because the rails are not the compile -----------

def _stub_compiler(tmp: Path, behaviour: str) -> Path:
  """A fake compile_modeld.py that fails in a specific, realistic way."""
  script = tmp / "stub_compiler.py"
  script.write_text(f"""
import os, sys, pickle, time
out = sys.argv[sys.argv.index("--output") + 1]
behaviour = {behaviour!r}
if behaviour == "hang":
    os.close(1); os.close(2)
    time.sleep(30)
if behaviour == "exit_nonzero":
    print("tinygrad: unsupported opset", file=sys.stderr); sys.exit(3)
if behaviour == "no_output":
    sys.exit(0)
if behaviour == "tiny":
    open(out, "wb").write(b"x" * 128); sys.exit(0)
if behaviour == "no_metadata":
    pickle.dump({{"runners": "x" * 200000}}, open(out, "wb")); sys.exit(0)
pickle.dump({{"metadata": {{"model": {{}}}}, "pad": "x" * 200000}}, open(out, "wb"))
""")
  return script


def _plan(tmp: Path):
  files = {r: tmp / f"{r}.onnx" for r in ("vision", "on_policy")}
  for p in files.values():
    p.write_bytes(b"")
  return plan_bundle(_bundle(["vision", "on_policy"], frame_skip=4), files)


def test_build_succeeds_and_places_artifact_atomically():
  with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    result = build(_plan(tmp), _stub_compiler(tmp, "ok"), tmp / "out",
                   model_size="512x256", camera_resolutions=["1928x1208"])
    assert result.artifact.exists() and result.size_bytes > 64 * 1024
    assert "unpickles" in result.checks and "carries metadata" in result.checks
    leftovers = list((tmp / "out").glob(".build-*")) + list((tmp / "out").glob(".*.new"))
    assert not leftovers, f"staging not cleaned: {leftovers}"


def test_build_failures_leave_nothing_behind():
  for behaviour, expect in [("exit_nonzero", "exited 3"), ("no_output", "produced no"),
                            ("tiny", "did not produce a model"), ("no_metadata", "metadata")]:
    with tempfile.TemporaryDirectory() as t:
      tmp = Path(t)
      try:
        build(_plan(tmp), _stub_compiler(tmp, behaviour), tmp / "out",
              model_size="512x256", camera_resolutions=["1928x1208"])
        raise AssertionError(f"{behaviour} should have raised")
      except BuildError as exc:
        assert expect in str(exc), f"{behaviour}: {exc}"
      out = tmp / "out"
      assert not list(out.glob("*.pkl")), f"{behaviour} left an artifact behind"
      assert not list(out.glob(".build-*")), f"{behaviour} left staging behind"


def test_build_timeout_kills_a_silent_compiler():
  with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    try:
      build(_plan(tmp), _stub_compiler(tmp, "hang"), tmp / "out",
            model_size="512x256", camera_resolutions=["1928x1208"], timeout=0.1)
      raise AssertionError("hung compiler should have timed out")
    except BuildError as exc:
      assert "exceeded" in str(exc), exc


def test_failed_build_restores_the_previously_active_model():
  with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    store = ModelStore(tmp / "models")
    oid = hashlib.sha256(b"x").hexdigest()
    store.record("old", {"files": [{"role": "vision", "filename": "v.onnx",
                                    "oid": oid, "size": 1}]})
    store.path_for("old").mkdir(parents=True, exist_ok=True)
    (store.path_for("old") / "v.onnx").write_bytes(b"x")
    store.set_active("old")

    try:
      build_and_activate(_plan(tmp), _stub_compiler(tmp, "exit_nonzero"), store,
                         model_size="512x256", camera_resolutions=["1928x1208"])
      raise AssertionError("should have raised")
    except BuildError:
      pass
    assert store.active() == "old", "a failed compile must not disturb the running model"


def test_smoke_test_rejects_an_unloadable_artifact():
  with tempfile.TemporaryDirectory() as t:
    bad = Path(t) / "bad.pkl"
    bad.write_bytes(b"\x80\x04garbage" + b"y" * 200000)
    try:
      smoke_test(bad)
      raise AssertionError("unloadable artifact must be rejected")
    except BuildError as exc:
      assert "unpickle" in str(exc), exc



def test_composed_manifest_carries_its_unattested_warning():
  """A composed bundle must not reach a compile without saying it never ran."""
  with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    files = {r: tmp / f"{r}.onnx" for r in ("vision", "on_policy")}
    for f in files.values():
      f.write_bytes(b"")
    manifest = _bundle(["vision", "on_policy"], frame_skip=4, source="composed", attested=False,
                       cautions=["cross-lineage: no shipped pairing is recorded for vision A and on_policy B"],
                       host_constants_by_role={"vision": {"LAT_SMOOTH_SECONDS": 0.0},
                                               "on_policy": {"LAT_SMOOTH_SECONDS": 0.1}})
    plan = plan_bundle(manifest, files)

  assert any("UNATTESTED" in w for w in plan.warnings), plan.warnings
  assert any("cross-lineage" in w for w in plan.warnings), "cautions must propagate"
  assert any("different host constants" in w for w in plan.warnings), plan.warnings
  # Disagreeing halves must not be merged into a configuration nobody ran; the fork chooses
  # from the per-role values instead.
  assert plan.host_constants == {}, plan.host_constants
  assert plan.host_constants_by_role["vision"]["LAT_SMOOTH_SECONDS"] == 0.0
  assert plan.host_constants_by_role["on_policy"]["LAT_SMOOTH_SECONDS"] == 0.1
  assert plan.to_dict()["host_constants_by_role"] == plan.host_constants_by_role


def test_agreeing_halves_collapse_to_one_configuration():
  """Collapsing is only honest when the halves agree — then it is one real configuration.

  The counterpart to the disagreement case above: refusing to report constants the halves both
  carry would push the fork into supplying its own, which is how a wrong smoothing constant
  gets in.
  """
  with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    files = {r: tmp / f"{r}.onnx" for r in ("vision", "on_policy")}
    for f in files.values():
      f.write_bytes(b"")
    both = {"LAT_SMOOTH_SECONDS": 0.2, "LONG_SMOOTH_SECONDS": 0.3}
    manifest = _bundle(["vision", "on_policy"], frame_skip=4, source="composed", attested=False,
                       host_constants_by_role={"vision": dict(both), "on_policy": dict(both)})
    plan = plan_bundle(manifest, files)

  assert plan.host_constants == both, plan.host_constants
  assert not any("different host constants" in w for w in plan.warnings), plan.warnings
  assert plan.host_constants_by_role["on_policy"] == both


def test_unknown_half_does_not_borrow_the_other_halfs_constants():
  with tempfile.TemporaryDirectory() as tmp:
    both = {"LAT_SMOOTH_SECONDS": 0.2}
    bundle = _bundle(["vision", "on_policy"], source="composed", attested=False,
                     host_constants_by_role={"vision": {}, "on_policy": both})
    files = {role: Path(tmp) / f"{role}.onnx" for role in ("vision", "on_policy")}
    for path in files.values():
      path.write_bytes(b"")
    plan = plan_bundle(bundle, files, host_frame_skip=4)
  assert plan.host_constants == {}
  assert any("different host constants" in warning for warning in plan.warnings)


def test_composed_frame_skip_is_compared_to_the_host():
  with tempfile.TemporaryDirectory() as tmp:
    bundle = _bundle(["vision", "on_policy"], source="composed", attested=False,
                     frame_skip_by_role={"vision": 1, "on_policy": 1})
    files = {role: Path(tmp) / f"{role}.onnx" for role in ("vision", "on_policy")}
    for path in files.values():
      path.write_bytes(b"")
    plan = plan_bundle(bundle, files, host_frame_skip=4)
  assert any("frame_skip mismatch" in warning for warning in plan.warnings)


if __name__ == "__main__":
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  for test in tests:
    test()
    print(f"  ok  {test.__name__}")
  print(f"\n{len(tests)} passed")
