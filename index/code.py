"""Short shareable codes for composed models.

A composed bundle is nothing but its selection, so a code only has to carry that: which oid
fills which role. Everything else — the checks, the lineage verdict, the manifest — is derived
when the code is redeemed.

The code carries **truncated** oids and is resolved against the catalog, which makes it a lookup
key rather than a description. That distinction is the safety property: a truncated or mistyped
code fails to resolve, or fails its checksum. It cannot quietly resolve to a *different* model,
which is the only failure mode that would matter on a device.

    OM2-4KUC-L4ZQ-RT7M

These get typed on a car touchscreen, so the format is squeezed hard:

  * the **role set** is one 5-bit shape rather than a byte per member — a composition is almost
    always vision+policy, so naming the shape costs less than naming each role;
  * oid prefixes are 3 bytes, giving 16.7M values against a few hundred files. A collision is
    ~1e-6 and is *detected* on resolve, never guessed at;
  * the checksum is 1 byte, because prefix resolution already rejects nearly all corruption —
    the checksum only has to catch the rare damage that still resolves.

Result: 13 characters for the common case, against 33 for the first format.

Base32 (A–Z, 2–7) has no 0/O or 1/I/L confusion and no case to get wrong.
"""

from __future__ import annotations

import base64
import hashlib

PREFIX = "OM2"
VERSION = 2            # 3 bits, packed with the shape
OID_BYTES = 3
CHECK_BYTES = 1
GROUP = 4              # display grouping; short runs are easier to thumb in

# Append only. Reordering silently changes what old codes mean — exactly the misresolution this
# format exists to prevent. Roles within a shape are the canonical encoding order.
SHAPES: tuple[tuple[str, ...], ...] = (
  ("vision", "on_policy"),
  ("vision", "on_policy", "off_policy"),
  ("supercombo",),
  ("vision", "off_policy"),
  ("big_vision", "big_on_policy"),
  ("big_vision", "big_on_policy", "big_off_policy"),
  ("big_supercombo",),
  ("dmonitoring",),
  ("navmodel",),
)


class CodeError(Exception):
  """The code is malformed, or does not resolve against this catalog."""


def _b32(data: bytes) -> str:
  return base64.b32encode(data).decode().rstrip("=")


def _unb32(text: str) -> bytes:
  try:
    return base64.b32decode(text + "=" * (-len(text) % 8))
  except Exception as exc:
    raise CodeError(f"not valid base32: {exc}") from exc


def _checksum(selection: dict[str, str]) -> bytes:
  """Over the *full* oids, so a prefix collision or a typo is caught on redemption."""
  joined = "".join(f"{r}:{selection[r]}" for r in sorted(selection))
  return hashlib.sha256(joined.encode()).digest()[:CHECK_BYTES]


def _shape_of(roles: set[str]) -> int:
  for index, shape in enumerate(SHAPES):
    if set(shape) == roles:
      return index
  raise CodeError(f"no code shape for role set {sorted(roles)}")


def encode(selection: dict[str, str]) -> str:
  """{role: full_oid} -> a shareable code."""
  if not selection:
    raise CodeError("nothing to encode")

  shape_index = _shape_of(set(selection))
  body = bytearray([(VERSION << 5) | shape_index])
  for role in SHAPES[shape_index]:
    oid = selection[role]
    if len(oid) < OID_BYTES * 2:
      raise CodeError(f"oid too short for role {role!r}")
    body += bytes.fromhex(oid[: OID_BYTES * 2])
  body += _checksum(selection)

  text = _b32(bytes(body))
  return f"{PREFIX}-" + "-".join(text[i:i + GROUP] for i in range(0, len(text), GROUP))


def decode(code: str) -> tuple[dict[str, str], str]:
  """A code -> ({role: oid_prefix}, checksum_hex). Prefixes still need resolving."""
  cleaned = code.strip().upper().replace(" ", "").replace("-", "")
  # Read off a screen and thumbed into a car, a code loses characters to lookalikes. These four
  # are safe to repair because the digit is not in base32's alphabet at all, so accepting it can
  # never collide with a legitimate code.
  #
  # 2/Z, 5/S, 6/G and 7/T are NOT repairable — both members are valid base32 — so a misread
  # there produces a prefix that resolves to nothing. That fails loudly, which is the property
  # worth protecting; it costs a retry, not a wrong model.
  cleaned = cleaned.translate(str.maketrans({"0": "O", "1": "I", "8": "B"}))
  # The prefix is for human recognition, not for the format — accept a code without it so
  # nobody has to thumb in three extra characters on a touchscreen. The version/shape byte and
  # the length check still reject anything that isn't one of ours.
  if cleaned.startswith(PREFIX):
    cleaned = cleaned[len(PREFIX):]
  if not cleaned:
    raise CodeError("empty code")

  raw = _unb32(cleaned)
  if len(raw) < 1 + OID_BYTES + CHECK_BYTES:
    raise CodeError("code is too short to contain a model")

  version, shape_index = raw[0] >> 5, raw[0] & 0x1F
  if version != VERSION:
    raise CodeError(f"unsupported code version {version}; this client speaks v{VERSION}")
  if shape_index >= len(SHAPES):
    raise CodeError(f"unknown shape {shape_index}; the code may be newer than this client")

  shape = SHAPES[shape_index]
  expected = 1 + OID_BYTES * len(shape) + CHECK_BYTES
  if len(raw) != expected:
    raise CodeError(f"code length {len(raw)} does not match its shape (expected {expected})")

  entries = raw[1:-CHECK_BYTES]
  selection = {role: entries[i * OID_BYTES:(i + 1) * OID_BYTES].hex()
               for i, role in enumerate(shape)}
  return selection, raw[-CHECK_BYTES:].hex()


def resolve(code: str, oids: list[str]) -> dict[str, str]:
  """A code + the catalog's oids -> {role: full_oid}.

  Raises rather than guessing on anything ambiguous. An unresolvable code is a far better
  outcome than one that resolves to weights the sender did not mean.
  """
  selection, expected_check = decode(code)

  resolved: dict[str, str] = {}
  for role, prefix in selection.items():
    matches = [o for o in oids if o.startswith(prefix)]
    if not matches:
      raise CodeError(f"no file in this catalog matches {role} prefix {prefix}")
    if len(matches) > 1:
      raise CodeError(f"ambiguous {role} prefix {prefix}: {len(matches)} matches")
    resolved[role] = matches[0]

  if _checksum(resolved).hex() != expected_check:
    raise CodeError("checksum mismatch: the code was mistyped, or resolved to the wrong files")
  return resolved
