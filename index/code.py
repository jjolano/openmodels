"""Short shareable codes for composed models.

A composed bundle is nothing but its selection, so a code only has to carry that: which oid
fills which role. Everything else — the checks, the lineage verdict, the manifest — is derived
when the code is redeemed.

The code carries **truncated** oids and is resolved against the catalog, which makes it a lookup
key rather than a description. That distinction is the safety property: a corrupted or truncated
code fails to resolve, or fails its checksum. It cannot quietly resolve to a *different* model,
which is the only failure mode that would matter on a device.

    OM1-CFOKA-6NDIF-4CBGL-A6QSC-VG7DQ-2XKMY-A

Uppercase base32 with dashes: unambiguous to read aloud, safe to paste, no mixed case.
"""

from __future__ import annotations

import base64
import hashlib
import struct

PREFIX = "OM1"
VERSION = 1
OID_BYTES = 6          # 48 bits; the archive has a few hundred files, so collisions are absurd
CHECK_BYTES = 2        # catches a typo that still resolves, and any prefix collision

# Index-encoded so a role costs one byte. Append only — never reorder, or old codes change
# meaning, which is precisely the silent misresolution this format exists to prevent.
ROLES = (
  "vision", "on_policy", "off_policy", "supercombo",
  "big_vision", "big_on_policy", "big_off_policy",
  "dmonitoring", "navmodel",
)


class CodeError(Exception):
  """The code is malformed, or does not resolve against this catalog."""


def _b32(data: bytes) -> str:
  return base64.b32encode(data).decode().rstrip("=")


def _unb32(text: str) -> bytes:
  pad = "=" * (-len(text) % 8)
  try:
    return base64.b32decode(text + pad)
  except Exception as exc:
    raise CodeError(f"not valid base32: {exc}") from exc


def encode(selection: dict[str, str]) -> str:
  """{role: full_oid} -> a shareable code."""
  if not selection:
    raise CodeError("nothing to encode")

  body = bytearray([VERSION])
  for role, oid in sorted(selection.items()):
    if role not in ROLES:
      raise CodeError(f"unknown role {role!r}")
    if len(oid) < OID_BYTES * 2:
      raise CodeError(f"oid too short for role {role!r}")
    body.append(ROLES.index(role))
    body += bytes.fromhex(oid[: OID_BYTES * 2])

  # Checksum over the *full* oids, so a prefix collision or a typo is caught on redemption.
  digest = hashlib.sha256("".join(f"{r}:{o}" for r, o in sorted(selection.items())).encode())
  body += digest.digest()[:CHECK_BYTES]

  text = _b32(bytes(body))
  groups = [text[i:i + 5] for i in range(0, len(text), 5)]
  return f"{PREFIX}-" + "-".join(groups)


def decode(code: str) -> dict[str, str]:
  """A code -> {role: oid_prefix}. Prefixes still need resolving against the catalog."""
  cleaned = code.strip().upper().replace(" ", "").replace("-", "")
  if not cleaned.startswith(PREFIX):
    raise CodeError(f"not an openmodels code (expected {PREFIX} prefix)")

  raw = _unb32(cleaned[len(PREFIX):])
  if len(raw) < 1 + 1 + OID_BYTES + CHECK_BYTES:
    raise CodeError("code is too short to contain a model")
  if raw[0] != VERSION:
    raise CodeError(f"unsupported code version {raw[0]}")

  entries, rest = raw[1:-CHECK_BYTES], raw[-CHECK_BYTES:]
  if len(entries) % (1 + OID_BYTES):
    raise CodeError("truncated code")

  selection: dict[str, str] = {}
  for i in range(0, len(entries), 1 + OID_BYTES):
    role_idx = entries[i]
    if role_idx >= len(ROLES):
      raise CodeError(f"unknown role index {role_idx}; the code may be newer than this client")
    selection[ROLES[role_idx]] = entries[i + 1:i + 1 + OID_BYTES].hex()

  if not selection:
    raise CodeError("code contains no roles")
  return {"_selection": selection, "_check": rest.hex()}  # type: ignore[return-value]


def resolve(code: str, oids: list[str]) -> dict[str, str]:
  """A code + the catalog's oids -> {role: full_oid}.

  Raises rather than guessing on anything ambiguous. An unresolvable code is a much better
  outcome than one that resolves to weights the sender did not mean.
  """
  decoded = decode(code)
  selection: dict[str, str] = decoded["_selection"]      # type: ignore[assignment]
  expected_check: str = decoded["_check"]                # type: ignore[assignment]

  resolved: dict[str, str] = {}
  for role, prefix in selection.items():
    matches = [o for o in oids if o.startswith(prefix)]
    if not matches:
      raise CodeError(f"no file in this catalog matches {role} prefix {prefix}")
    if len(matches) > 1:
      raise CodeError(f"ambiguous {role} prefix {prefix}: {len(matches)} matches")
    resolved[role] = matches[0]

  digest = hashlib.sha256("".join(f"{r}:{o}" for r, o in sorted(resolved.items())).encode())
  if digest.digest()[:CHECK_BYTES].hex() != expected_check:
    raise CodeError("checksum mismatch: the code was mistyped, or resolved to the wrong files")
  return resolved
