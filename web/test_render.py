#!/usr/bin/env python3
"""Checks for the display layer: titles stay honest, listings stay newest-first."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients import reference  # noqa: E402
from web import render  # noqa: E402


def bundle(name, date, files=(("vision", "driving_vision.onnx"),)):
  return {
    "bundle_id": "b" * 16, "name": name, "slug": "s", "kind": "driving",
    "family": "split", "variant": "standard", "in_head": True,
    "files": [{"role": r, "filename": f, "oid": "a" * 64, "size": 1} for r, f in files],
    "occurrences": [{"commit": "c" * 40, "date": date, "pr": None, "subject": name,
                     "status": "merged"}],
    "introduced_by": {"commit": "c" * 40, "date": date, "pr": None},
  }


def test_pretty_name_only_rewrites_training_run_refs():
  run = bundle("0b6e45f8-21c1-408a-b61d-3dce02a69d23/500", "2026-01-01T00:00:00Z")
  assert render.pretty_name(run) == "Training run 0b6e45f8 · step 500"
  # No step: still a reference, still no invented description.
  del run["name"]
  run["name"] = "0b6e45f8-21c1-408a-b61d-3dce02a69d23"
  assert render.pretty_name(run) == "Training run 0b6e45f8"
  # Anything comma actually titled is left exactly alone.
  for title in ("Firehose model", "Tomb Raider 14", "modeld: update driving model", "big"):
    assert render.pretty_name(bundle(title, "2026-01-01T00:00:00Z")) == title


def test_role_labels_reach_the_tables():
  detail = render.render_detail(
    {"files": [], "attested_pairings": []},
    bundle("Firehose model", "2026-01-01T00:00:00Z",
           files=(("vision", "driving_vision.onnx"), ("on_policy", "driving_on_policy.onnx"))))
  assert ">on-policy<" in detail and ">on_policy<" not in detail


def test_browse_and_client_list_newest_first():
  old, new = bundle("old", "2019-01-01T00:00:00Z"), bundle("new", "2026-01-01T00:00:00Z")
  index = {"bundles": [old, new], "files": [], "attested_pairings": [],
           "generated_at": "2026-01-02T00:00:00Z"}
  page = render.render_browse(index)
  assert page.index(">new<") < page.index(">old<")
  assert [b["name"] for b in reference.select({"bundles": [old, new]})] == ["new", "old"]


if __name__ == "__main__":
  test_pretty_name_only_rewrites_training_run_refs()
  test_role_labels_reach_the_tables()
  test_browse_and_client_list_newest_first()
  print("web/render checks passed")
