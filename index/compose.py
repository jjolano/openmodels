"""Assemble a bundle from halves that were indexed separately.

comma ships one vision encoder against several policies, so combining halves is how upstream
already works — not a novelty. What this module adds is saying, precisely, whether a particular
combination is one that *shipped*.

Two ideas carry the weight:

  **Attested** means these two checkpoints co-occurred in an upstream bundle. It is a provenance
  fact, read from `model_checkpoint`, not a compatibility judgement.

  **Cross-lineage** means they did not. Such a pair still loads and runs: the 512-float latent
  between vision and policy is untyped, so a mismatched encoder produces confident nonsense
  rather than an error. That is exactly why it is called out loudly instead of being blocked —
  the failure is silent, so the warning cannot be.

Nothing composed here has ever been driven. `attested: false` on the result is not a formality.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# The policy consumes the vision encoder's hidden state through this many floats. A mismatch is
# structural and fatal; equality proves only that the plumbing fits, never that the numbers mean
# the same thing on both sides.
SEAM_KEY = "hidden_state"


class ComposeError(Exception):
  """The requested combination cannot be assembled."""


def bundle_id(members: list[dict[str, Any]]) -> str:
  """Same content-addressing as an upstream bundle, so composed ids need no registry."""
  key = sorted((m["role"], m["filename"], m["oid"]) for m in members)
  return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def attested_pairings(bundles: list[dict[str, Any]],
                      files_by_oid: dict[str, dict[str, Any]]) -> list[list[str]]:
  """Every (vision_ckpt, policy_ckpt) pair upstream actually shipped.

  Two sources, both provenance:
    - halves that appeared in the same bundle, and
    - a fused supercombo, which names its vision and policy checkpoints in its own metadata.
  """
  pairs: set[tuple[str, str]] = set()

  for bundle in bundles:
    ckpts: dict[str, str] = {}
    for member in bundle.get("files", []):
      record = files_by_oid.get(member["oid"], {})
      lineage = (record.get("metadata") or {}).get("lineage")
      if not lineage:
        continue
      if lineage.get("fused"):
        pairs.add((lineage["vision"], lineage["policy"]))
      elif "self" in lineage:
        ckpts[member["role"]] = lineage["self"]

    vision = ckpts.get("vision")
    if vision:
      for role, ckpt in ckpts.items():
        if role != "vision":
          pairs.add((vision, ckpt))

  return sorted([list(p) for p in pairs])


def partners_of(checkpoint: str, pairings: list[list[str]]) -> dict[str, list[str]]:
  """What a given checkpoint has shipped alongside — the useful thing to show a browser."""
  return {
    "as_vision": sorted({p[1] for p in pairings if p[0] == checkpoint}),
    "as_policy": sorted({p[0] for p in pairings if p[1] == checkpoint}),
  }


def _seam_width(metadata: dict[str, Any]) -> int | None:
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


def _buffer_shape(metadata: dict[str, Any]) -> list[int] | None:
  shape = (metadata.get("input_shapes") or {}).get("features_buffer")
  return shape if shape and len(shape) == 3 else None


def compose(selection: dict[str, str], files_by_oid: dict[str, dict[str, Any]],
            pairings: list[list[str]]) -> dict[str, Any]:
  """Build a composed manifest from {role: oid}.

  Refuses only what cannot work — an unknown oid, an unusable role set, or a seam width that
  does not line up. Everything else that merits attention is returned as a caution, because a
  combination this data cannot vouch for is still one a fork may legitimately want to try.
  """
  if not selection:
    raise ComposeError("no files selected")

  members: list[dict[str, Any]] = []
  for role, oid in sorted(selection.items()):
    record = files_by_oid.get(oid)
    if record is None:
      raise ComposeError(f"unknown oid for role {role!r}: {oid}")
    filename = record["filenames"][0] if record.get("filenames") else f"{role}.onnx"
    members.append({"role": role, "filename": filename, "oid": oid,
                    "size": record["size"], "metadata": record.get("metadata") or {}})

  # Hardware target and model kind are properties of the *filename*, not of the role: the indexer
  # reads big_driving_vision.onnx as ("driving", "big", "vision"). A big_ half is a USBGPU/AMD
  # artifact and a standard one is QCOM, so a bundle that mixes them cannot run anywhere — the
  # same reason the indexer never groups them together. Deferred import: indexer imports us.
  from index.indexer import classify
  specs = [s for s in (classify(m["filename"]) for m in members) if s]
  kinds = {s[0] for s in specs}
  variants = {s[1] for s in specs}
  if len(variants) > 1:
    raise ComposeError("cannot combine big_ (USBGPU/AMD) and standard (QCOM) halves: they target "
                       "different hardware and are different architectures")
  if len(kinds) > 1:
    raise ComposeError(f"cannot combine {sorted(kinds)} models in one bundle")

  roles = {m["role"] for m in members}
  if "supercombo" in roles and len(roles) > 1:
    raise ComposeError("a supercombo is self-contained and cannot be combined with other halves")
  if "supercombo" not in roles and "vision" not in roles:
    raise ComposeError(f"a composed driving model needs a vision half; got {sorted(roles)}")
  if "supercombo" not in roles and not ({"on_policy", "off_policy"} & roles):
    raise ComposeError(f"a composed driving model needs a policy half; got {sorted(roles)}")

  checks: dict[str, Any] = {}
  cautions: list[str] = []

  by_role = {m["role"]: m for m in members}
  vision = by_role.get("vision")
  policies = [m for m in members if m["role"] in ("on_policy", "off_policy")]

  # --- structural: does the latent plumbing fit? ---
  if vision is not None:
    width = _seam_width(vision["metadata"])
    checks["seam_width"] = width
    if width is None:
      cautions.append("vision exposes no usable hidden_state slice; the seam cannot be checked")
    for policy in policies:
      shape = _buffer_shape(policy["metadata"])
      if shape is None:
        cautions.append(f"{policy['role']} declares no features_buffer; seam unverifiable")
        continue
      depth, latent = shape[1], shape[2]
      checks.setdefault("buffer_depth", {})[policy["role"]] = depth
      if width is not None and latent != width:
        raise ComposeError(
          f"seam mismatch: vision hidden_state is {width} wide, "
          f"{policy['role']} features_buffer expects {latent}"
        )
      # Depth drives cadence: derive_frame_skip returns 1 at >=99, else 4.
      implied = 1 if depth >= 99 else 4
      checks.setdefault("implied_frame_skip", {})[policy["role"]] = implied

  depths = set((checks.get("buffer_depth") or {}).values())
  if len(depths) > 1:
    cautions.append(
      f"policies disagree on features_buffer depth {sorted(depths)}; they expect different "
      f"temporal cadences and cannot share one buffer"
    )

  # --- provenance: did this pairing ever ship? ---
  known = {tuple(p) for p in pairings}
  lineage: dict[str, Any] = {}
  if vision is not None:
    v_line = (vision["metadata"].get("lineage") or {})
    v_ckpt = v_line.get("self") or v_line.get("vision")
    lineage["vision"] = v_ckpt
    for policy in policies:
      p_line = (policy["metadata"].get("lineage") or {})
      p_ckpt = p_line.get("self") or p_line.get("policy")
      lineage[policy["role"]] = p_ckpt
      if not v_ckpt or not p_ckpt:
        cautions.append(
          f"lineage unknown for vision or {policy['role']}; cannot tell whether these halves "
          f"were built for each other"
        )
      elif (v_ckpt, p_ckpt) not in known:
        cautions.append(
          f"cross-lineage: vision {v_ckpt} and {policy['role']} {p_ckpt} never shipped together. "
          f"The latent between them is untyped, so this will load and run regardless of whether "
          f"the numbers mean the same thing."
        )
  checks["lineage"] = lineage
  checks["attested_pairing"] = all(
    (lineage.get("vision"), lineage.get(p["role"])) in known for p in policies
  ) if (policies and vision is not None) else False

  # Constants stay per-half: they came from different commits, and silently merging them would
  # invent a configuration nobody ran.
  constants = {m["role"]: (m["metadata"].get("host_constants") or {}) for m in members}

  return {
    "bundle_id": bundle_id(members),
    "source": "composed",
    "attested": False,        # nothing composed here has ever been driven
    "files": [{k: m[k] for k in ("role", "filename", "oid", "size")} for m in members],
    "checks": checks,
    "cautions": cautions,
    "host_constants_by_role": constants,
    "verify": "sha256 of each downloaded file MUST equal its oid; refuse the blob otherwise",
    "disclaimer": (
      "Composed from indexed halves. This exact combination has never run upstream and is not "
      "attested by anyone. Structural checks passing does not mean it drives correctly."
    ),
  }
