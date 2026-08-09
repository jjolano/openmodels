"""Short shareable codes for composed models.

A composed bundle is nothing but its selection, so a code only has to carry that: which oid
fills which role. Everything else — the checks, the lineage verdict, the manifest — is derived
when the code is redeemed.

The code carries **truncated** oids and is resolved against the catalog, which makes it a lookup
key rather than a description. That distinction is the safety property: a mistyped code fails to
resolve, or fails its checksum. It cannot quietly resolve to a *different* model, which is the
only failure mode that would matter on a device.

    OM3-XKMV-9CPA-3RWF-N7

These get read off a phone and thumbed into a car touchscreen, so the format is squeezed and the
alphabet is chosen for transcription rather than for byte alignment:

  * the **role set** is one 5-bit shape rather than a byte per member;
  * oid prefixes are 3 bytes — 16.7M values against a few hundred files, and a collision is
    detected on resolve, never guessed at;
  * the checksum is 1 byte, since prefix resolution already rejects nearly all corruption.

**Every confusable letter is excluded from the alphabet**, which is what makes misreads
self-correcting rather than merely loud: because `O` is not a legal character, accepting a typed
`O` as `0` cannot collide with any valid code. Base32 could not do this — it contains both `2`
and `Z`, both `5` and `S` — so a misread there was unrepairable. Base26 costs one extra
character (14 rather than 13) and removes the ambiguity entirely.
"""

from __future__ import annotations

import hashlib
import math

PREFIX = "OM3"
VERSION = 3            # 3 bits, packed with the shape
OID_BYTES = 3
CHECK_BYTES = 1
GROUP = 4

# 10 digits + 16 letters. Every letter that resembles a digit is gone: B G I L O Q S T U Z.
# What remains has no pair a person can confuse when reading a code aloud or off a screen.
ALPHABET = "0123456789ACDEFHJKMNPRVWXY"
BASE = len(ALPHABET)
_VALUES = {c: i for i, c in enumerate(ALPHABET)}

# Typed character -> what it must have been. Unambiguous precisely because none of the keys are
# legal in the alphabet, so no valid code is ever altered.
_REPAIR = str.maketrans({
  "O": "0", "Q": "0",      # round letters -> zero
  "I": "1", "L": "1",      # tall strokes -> one
  "Z": "2", "S": "5", "G": "6", "T": "7", "B": "8",
  "U": "V",                # only letter pair worth repairing
})

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

# Shapes 4-8 name roles the index never emits. `big` is a bundle-level *variant*, not a role —
# indexer.ROLE_PATTERNS reads big_driving_vision.onnx as ("driving", "big", "vision"), so a big_
# composition is expressed with the ordinary roles and the variant follows from the files. The
# remaining two are single-file models with nothing to compose. They stay in SHAPES because the
# table is append-only; they are simply never minted, so no code can be issued that compose()
# would then refuse to redeem.
_MINTABLE = frozenset({0, 1, 2, 3})


class CodeError(Exception):
  """The code is malformed, or does not resolve against this catalog."""


def _chars_for(nbytes: int) -> int:
  """Fixed width per payload size, so the decoder can infer the size from the length."""
  return math.ceil(nbytes * 8 / math.log2(BASE))


# char count -> byte count, for every payload size a shape can produce
_SIZES = {_chars_for(n): n for n in
          {1 + OID_BYTES * len(s) + CHECK_BYTES for s in SHAPES}}


def _encode_base26(data: bytes) -> str:
  value = int.from_bytes(data, "big")
  out = []
  for _ in range(_chars_for(len(data))):
    value, digit = divmod(value, BASE)
    out.append(ALPHABET[digit])
  return "".join(reversed(out))


def _decode_base26(text: str) -> bytes:
  nbytes = _SIZES.get(len(text))
  if nbytes is None:
    raise CodeError(f"code is {len(text)} characters; no model code is that length")
  value = 0
  for char in text:
    digit = _VALUES.get(char)
    if digit is None:
      raise CodeError(f"{char!r} is not a valid character in a model code")
    value = value * BASE + digit
  if value >= 1 << (nbytes * 8):
    raise CodeError("code does not decode to a valid payload")
  return value.to_bytes(nbytes, "big")


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
  if shape_index not in _MINTABLE:
    raise CodeError(f"shape {shape_index} ({', '.join(SHAPES[shape_index])}) is reserved but not "
                    f"composable, so no code is issued for it")
  body = bytearray([(VERSION << 5) | shape_index])
  for role in SHAPES[shape_index]:
    oid = selection[role]
    if len(oid) < OID_BYTES * 2:
      raise CodeError(f"oid too short for role {role!r}")
    body += bytes.fromhex(oid[: OID_BYTES * 2])
  body += _checksum(selection)

  text = _encode_base26(bytes(body))
  return f"{PREFIX}-" + "-".join(text[i:i + GROUP] for i in range(0, len(text), GROUP))


def decode(code: str) -> tuple[dict[str, str], str]:
  """A code -> ({role: oid_prefix}, checksum_hex). Prefixes still need resolving."""
  cleaned = code.strip().upper().replace(" ", "").replace("-", "")
  # The prefix aids human recognition; it is not part of the format, so nobody has to thumb in
  # three extra characters.
  if cleaned.startswith(PREFIX):
    cleaned = cleaned[len(PREFIX):]
  if not cleaned:
    raise CodeError("empty code")

  raw = _decode_base26(cleaned.translate(_REPAIR))

  version, shape_index = raw[0] >> 5, raw[0] & 0x1F
  if version != VERSION:
    raise CodeError(f"unsupported code version {version}; this client speaks v{VERSION}")
  if shape_index >= len(SHAPES):
    raise CodeError(f"unknown shape {shape_index}; the code may be newer than this client")

  shape = SHAPES[shape_index]
  expected = 1 + OID_BYTES * len(shape) + CHECK_BYTES
  if len(raw) != expected:
    raise CodeError(f"code length does not match its shape (expected {expected} bytes)")

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
