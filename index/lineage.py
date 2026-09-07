"""Recorded upstream checkpoint pairings and structural metadata."""
from typing import Any

SEAM_KEY = "hidden_state"

def shipped_upstream(bundle: dict[str, Any]) -> bool:
  """Whether this exact file set ever landed upstream, including one later reverted."""
  return any(o.get("status") in ("merged", "reverted")
             for o in bundle.get("occurrences", ()))


def attested_pairings(bundles: list[dict[str, Any]],
                      files_by_oid: dict[str, dict[str, Any]]) -> list[list[str]]:
  """Every (vision_ckpt, policy_ckpt) pair upstream actually shipped.

  Two sources, both provenance:
    - halves that appeared in the same bundle, and
    - a fused supercombo, which names its vision and policy checkpoints in its own metadata.
  """
  pairs: set[tuple[str, str]] = set()

  for bundle in bundles:
    if not shipped_upstream(bundle):
      continue
    ckpts: dict[str, str] = {}
    for member in bundle.get("files", []):
      record = files_by_oid.get(member["oid"], {})
      lineage = (record.get("metadata") or {}).get("lineage")
      if not lineage:
        continue
      if member.get("role") == "supercombo" and lineage.get("fused"):
        pairs.add((lineage["vision"], lineage["policy"]))
      elif member.get("role") in ("vision", "on_policy", "off_policy") and "self" in lineage:
        ckpts[member["role"]] = lineage["self"]

    vision = ckpts.get("vision")
    if vision:
      for role, ckpt in ckpts.items():
        if role != "vision":
          pairs.add((vision, ckpt))

  return sorted([list(p) for p in pairs])


def seam_width(metadata: dict[str, Any]) -> int | None:
  """Width of the vision→policy seam, resolving Python slice semantics.

  These are real `slice` objects, so a bound may be negative and count from the end
  (`hidden_state: [1064, -120]`). Subtracting the raw numbers yields nonsense, so negative
  bounds are resolved against the model's declared output length. Returns None when the length
  is unknown rather than guessing — an unverifiable seam is a caution, not a fabricated width.
  """
  bounds = (metadata.get("output_slices") or {}).get(SEAM_KEY)
  if not bounds or bounds[0] is None or bounds[1] is None:
    return None

  start, stop = bounds[0], bounds[1]
  if start < 0 or stop < 0:
    shapes = metadata.get("output_shapes") or {}
    total = next((s[-1] for s in shapes.values() if s and isinstance(s[-1], int)), None)
    if total is None:
      return None
    start = start + total if start < 0 else start
    stop = stop + total if stop < 0 else stop

  width = stop - start
  return width if width > 0 else None


