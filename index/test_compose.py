#!/usr/bin/env python3
"""Self-check for lineage and composition. Run: python3 index/test_compose.py

The property under test throughout: a pairing is *attested* only when upstream actually shipped
it. Everything else composes, but says so.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from index.compose import (  # noqa: E402
  ComposeError, attested_pairings, bundle_id, compose, partners_of,
)
from index.metadata import parse_lineage  # noqa: E402

V1 = "1c8e05fa-bb24-42ad-af22-c0e6d59a5df5/100"     # a real vision encoder
P1 = "62c0d269-6c47-436c-aa61-02165681f88b/400"     # shipped with V1
P2 = "3fbe51c8-750f-4808-addb-21e923d8fdff/400"     # also shipped with V1
V2 = "6a7d09ad-bcc9-43bc-916d-29287e60cee2/200"     # a different encoder
PX = "a27b3122-733e-4a65-938b-acfebebbe5e8/100"     # shipped with V2, never with V1


def test_parse_lineage_handles_every_real_shape():
  single = parse_lineage(V2)
  assert single == {"checkpoints": [V2], "fused": False, "self": V2}, single

  fused = parse_lineage(f"{V2}/{PX}")
  assert fused["fused"] and fused["vision"] == V2 and fused["policy"] == PX, fused

  # Older models genuinely lack it; unknown must stay unknown rather than be invented.
  assert parse_lineage(None) is None
  assert parse_lineage("") is None
  assert parse_lineage("not-a-checkpoint") is None      # odd number of parts
  assert parse_lineage("a/1/b") is None                 # ragged


def _file(oid, ckpt=None, *, seam=None, buffer_depth=None, name="m.onnx", size=1000):
  meta = {"input_shapes": {}, "output_slices": {}, "model_checkpoint": ckpt,
          "lineage": parse_lineage(ckpt)}
  if seam is not None:
    meta["output_slices"]["hidden_state"] = [0, seam, None]
  if buffer_depth is not None:
    meta["input_shapes"]["features_buffer"] = [1, buffer_depth, 512]
  return {"oid": oid, "size": size, "filenames": [name], "metadata": meta}


def test_attested_pairings_from_cooccurrence_and_from_fusion():
  files = {
    "v": _file("v", V1, seam=512), "p": _file("p", P1, buffer_depth=25),
    "sc": _file("sc", f"{V2}/{PX}", seam=512, buffer_depth=24),
  }
  bundles = [
    {"files": [{"role": "vision", "oid": "v"}, {"role": "on_policy", "oid": "p"}]},
    {"files": [{"role": "supercombo", "oid": "sc"}]},
  ]
  pairs = attested_pairings(bundles, files)
  assert [V1, P1] in pairs, pairs        # co-occurred in a bundle
  assert [V2, PX] in pairs, pairs        # named inside the supercombo's own checkpoint


def test_partners_of_answers_the_browsing_question():
  pairs = [[V1, P1], [V1, P2], [V2, PX]]
  assert partners_of(V1, pairs)["as_vision"] == sorted([P1, P2])
  assert partners_of(P1, pairs)["as_policy"] == [V1]
  assert partners_of("unknown/1", pairs) == {"as_vision": [], "as_policy": []}


def _files():
  return {
    "vis1": _file("vis1", V1, seam=512, name="driving_vision.onnx"),
    "pol1": _file("pol1", P1, buffer_depth=25, name="driving_on_policy.onnx"),
    "polx": _file("polx", PX, buffer_depth=25, name="driving_on_policy.onnx"),
    "pol99": _file("pol99", P2, buffer_depth=99, name="driving_on_policy.onnx"),
    "wide": _file("wide", P2, buffer_depth=25, name="w.onnx"),
    "sc": _file("sc", f"{V2}/{PX}", seam=512, name="driving_supercombo.onnx"),
  }


def test_attested_pairing_composes_without_a_cross_lineage_caution():
  out = compose({"vision": "vis1", "on_policy": "pol1"}, _files(), [[V1, P1]])
  assert out["checks"]["attested_pairing"] is True
  assert not any("cross-lineage" in c for c in out["cautions"]), out["cautions"]
  assert out["source"] == "composed"
  # Composed is never attested: this file set has still never been driven.
  assert out["attested"] is False


def test_cross_lineage_composes_but_says_so():
  out = compose({"vision": "vis1", "on_policy": "polx"}, _files(), [[V1, P1]])
  assert out["checks"]["attested_pairing"] is False
  assert any("cross-lineage" in c for c in out["cautions"]), out["cautions"]
  assert any("untyped" in c for c in out["cautions"]), "must explain why it won't error"


def test_unknown_lineage_is_a_caution_not_a_refusal():
  files = _files()
  files["nolin"] = _file("nolin", None, buffer_depth=25)
  out = compose({"vision": "vis1", "on_policy": "nolin"}, files, [[V1, P1]])
  assert any("lineage unknown" in c for c in out["cautions"]), out["cautions"]


def test_seam_width_mismatch_is_refused():
  files = _files()
  files["narrow"] = _file("narrow", P1, buffer_depth=25)
  files["narrow"]["metadata"]["input_shapes"]["features_buffer"] = [1, 25, 256]
  try:
    compose({"vision": "vis1", "on_policy": "narrow"}, files, [])
    raise AssertionError("a 512 vision must not compose with a 256 policy")
  except ComposeError as exc:
    assert "seam mismatch" in str(exc), exc


def test_buffer_depth_implies_frame_skip():
  out = compose({"vision": "vis1", "on_policy": "pol99"}, _files(), [])
  assert out["checks"]["buffer_depth"]["on_policy"] == 99
  assert out["checks"]["implied_frame_skip"]["on_policy"] == 1, "99 deep means 20Hz cadence"
  shallow = compose({"vision": "vis1", "on_policy": "pol1"}, _files(), [])
  assert shallow["checks"]["implied_frame_skip"]["on_policy"] == 4


def test_policies_disagreeing_on_depth_are_flagged():
  out = compose({"vision": "vis1", "on_policy": "pol1", "off_policy": "pol99"}, _files(), [])
  assert any("disagree" in c for c in out["cautions"]), out["cautions"]


def test_role_set_must_be_runnable():
  for selection, expect in [
    ({"on_policy": "pol1"}, "vision half"),
    ({"vision": "vis1"}, "policy half"),
    ({"vision": "vis1", "supercombo": "sc"}, "self-contained"),
    ({}, "no files"),
  ]:
    try:
      compose(selection, _files(), [])
      raise AssertionError(f"{selection} should not compose")
    except ComposeError as exc:
      assert expect in str(exc), f"{selection}: {exc}"


def test_unknown_oid_is_refused():
  try:
    compose({"vision": "vis1", "on_policy": "ghost"}, _files(), [])
    raise AssertionError("unknown oid must be refused")
  except ComposeError as exc:
    assert "unknown oid" in str(exc)


def test_constants_stay_separated_by_role():
  files = _files()
  files["vis1"]["metadata"]["host_constants"] = {"LAT_SMOOTH_SECONDS": 0.0}
  files["pol1"]["metadata"]["host_constants"] = {"LAT_SMOOTH_SECONDS": 0.1}
  out = compose({"vision": "vis1", "on_policy": "pol1"}, files, [[V1, P1]])
  # Merging these would invent a configuration nobody ran.
  assert out["host_constants_by_role"]["vision"]["LAT_SMOOTH_SECONDS"] == 0.0
  assert out["host_constants_by_role"]["on_policy"]["LAT_SMOOTH_SECONDS"] == 0.1


def test_bundle_id_is_content_addressed_and_order_free():
  a = compose({"vision": "vis1", "on_policy": "pol1"}, _files(), [])
  b = compose({"on_policy": "pol1", "vision": "vis1"}, _files(), [])
  c = compose({"vision": "vis1", "on_policy": "polx"}, _files(), [])
  assert a["bundle_id"] == b["bundle_id"], "selection order must not change identity"
  assert a["bundle_id"] != c["bundle_id"], "different members must differ"
  assert bundle_id(a["files"]) == a["bundle_id"]


def test_seam_width_resolves_negative_slice_bounds():
  """output_slices are real Python slices, so a bound may count from the end."""
  from index.compose import _seam_width
  # hidden_state: [1064, -120] against a 1576-wide output -> 1576-120-1064 = 392
  meta = {"output_slices": {"hidden_state": [1064, -120, None]},
          "output_shapes": {"outputs": [1, 1576]}}
  assert _seam_width(meta) == 392, _seam_width(meta)

  # plain positive bounds are unaffected
  assert _seam_width({"output_slices": {"hidden_state": [1064, 1576, None]},
                      "output_shapes": {"outputs": [1, 1576]}}) == 512

  # unknown output length -> unverifiable, never a fabricated width
  assert _seam_width({"output_slices": {"hidden_state": [1064, -120, None]}}) is None
  # a slice that resolves to nothing is not a seam
  assert _seam_width({"output_slices": {"hidden_state": [500, 100, None]},
                      "output_shapes": {"outputs": [1, 1576]}}) is None


if __name__ == "__main__":
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  for test in tests:
    test()
    print(f"  ok  {test.__name__}")
  print(f"\n{len(tests)} passed")
