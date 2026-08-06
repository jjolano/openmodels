"""Extract model-coupled host constants from openpilot source at a given commit.

These are the compatibility payload. `LAT_SMOOTH_SECONDS` / `LONG_SMOOTH_SECONDS` are read by
controlsd to compute lateral delay, and comma changes them *in the same commit that swaps a
model* — so weights without their constants are an incomplete artifact. sunnypilot's per-bundle
`overrides` are these same values, copied by hand.

Parsed with ast.literal_eval over the source text. The tree is never imported or executed:
indexed refs include arbitrary PR heads.
"""

from __future__ import annotations

import ast
from typing import Any, NamedTuple

# name -> candidate paths, newest era first. openpilot moved everything under openpilot/ in
# #38223, and modeld moved out of the repo root long before that.
CONSTANT_SOURCES: dict[str, tuple[str, ...]] = {
  "LAT_SMOOTH_SECONDS": (
    "openpilot/selfdrive/modeld/modeld.py",
    "selfdrive/modeld/modeld.py",
  ),
  "LONG_SMOOTH_SECONDS": (
    "openpilot/selfdrive/modeld/modeld.py",
    "selfdrive/modeld/modeld.py",
  ),
  "MODEL_RUN_FREQ": (
    "openpilot/selfdrive/modeld/constants.py",
    "selfdrive/modeld/constants.py",
  ),
  "MODEL_CONTEXT_FREQ": (
    "openpilot/selfdrive/modeld/constants.py",
    "selfdrive/modeld/constants.py",
  ),
  "MODEL_FREQ": (
    "openpilot/selfdrive/modeld/constants.py",
    "selfdrive/modeld/constants.py",
  ),
}


class Found(NamedTuple):
  value: Any
  line: int


def extract_from_source(source: str, names: set[str]) -> dict[str, Found]:
  """Find literal assignments for `names`, at module level or inside a class body.

  openpilot keeps LAT_SMOOTH_SECONDS at module level in modeld.py but MODEL_CONTEXT_FREQ
  inside `class ModelConstants`, so both scopes are searched. Only literals are accepted — a
  computed value is reported as absent rather than guessed at.
  """
  try:
    tree = ast.parse(source)
  except SyntaxError:
    return {}

  found: dict[str, Found] = {}

  def visit_body(body):
    for node in body:
      if isinstance(node, ast.ClassDef):
        visit_body(node.body)
        continue
      if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        continue
      targets = node.targets if isinstance(node, ast.Assign) else [node.target]
      if node.value is None:
        continue
      for target in targets:
        if isinstance(target, ast.Name) and target.id in names and target.id not in found:
          try:
            found[target.id] = Found(ast.literal_eval(node.value), node.lineno)
          except (ValueError, SyntaxError):
            pass  # computed, not literal — treated as absent

  visit_body(tree.body)
  return found


def extract(read_file, names: set[str] | None = None) -> dict[str, Any]:
  """Resolve constants using `read_file(path) -> str | None`.

  Returns {"values": {...}, "sources": {name: "path:line"}, "missing": [...]}.

  A name that cannot be found is reported as missing and its value is **absent**, never
  defaulted. A wrong smoothing constant silently changes steering, so a gap must be visible.
  """
  names = names or set(CONSTANT_SOURCES)
  values: dict[str, Any] = {}
  sources: dict[str, str] = {}

  for name in sorted(names):
    for path in CONSTANT_SOURCES.get(name, ()):
      source = read_file(path)
      if source is None:
        continue
      hit = extract_from_source(source, {name}).get(name)
      if hit is not None:
        values[name] = hit.value
        sources[name] = f"{path}:{hit.line}"
        break

  missing = sorted(set(names) - set(values))
  result: dict[str, Any] = {"values": values, "sources": sources, "missing": missing}

  # frame_skip is what the build actually passes to the compiler (SConscript), so derive it
  # from the host constants rather than guessing it from a tensor shape.
  run, ctx = values.get("MODEL_RUN_FREQ"), values.get("MODEL_CONTEXT_FREQ")
  if isinstance(run, int) and isinstance(ctx, int) and ctx > 0:
    result["frame_skip"] = run // ctx
    sources["frame_skip"] = "derived: MODEL_RUN_FREQ // MODEL_CONTEXT_FREQ"

  return result
