"""Extract metadata from openpilot ONNX models without executing anything in them.

Deliberately dependency-free: no onnx, no protobuf, no tinygrad. The indexer runs on plain
CI runners and must never need comma hardware, so this hand-decodes the protobuf wire format.

Everything here treats its input as hostile. Blobs come from arbitrary PR heads while CI holds
publish credentials.
"""

from __future__ import annotations

import base64
import io
import pickle
from typing import Any, Iterator, NamedTuple

# ONNX TensorProto.DataType -> name. Only the types openpilot models actually use are named;
# anything else is reported as its raw enum so an unknown dtype is visible rather than silent.
_DTYPES = {
  1: "float32", 2: "uint8", 3: "int8", 4: "uint16", 5: "int16", 6: "int32", 7: "int64",
  8: "string", 9: "bool", 10: "float16", 11: "float64", 12: "uint32", 13: "uint64",
  14: "complex64", 15: "complex128", 16: "bfloat16",
}

# A crafted model could otherwise make us allocate unboundedly.
MAX_PICKLE_BYTES = 1 << 20
MAX_SLICES = 4096


class UnsafePickle(Exception):
  """The pickle stream tried to reach something outside the allowlist."""


class _SliceOnlyUnpickler(pickle.Unpickler):
  """Unpickler that can construct nothing but `slice`.

  `output_slices` is a base64-encoded pickle embedded in the ONNX metadata, i.e. attacker
  controlled. Every opcode that can execute code (GLOBAL, STACK_GLOBAL, REDUCE, INST, OBJ,
  NEWOBJ, EXT1/2/4) resolves its callable through find_class, so refusing everything except
  `builtins.slice` leaves no path to execution. `slice(a, b, c)` cannot itself run anything.
  """

  def find_class(self, module: str, name: str) -> Any:
    if (module, name) != ("builtins", "slice"):
      raise UnsafePickle(f"refused to resolve {module}.{name}")
    return slice

  def persistent_load(self, pid: Any) -> Any:
    raise UnsafePickle("refused persistent id")


def loads_output_slices(encoded: str) -> dict[str, list[int | None]]:
  """Decode the base64+pickle `output_slices` metadata value safely.

  Returns {name: [start, stop, step]}. Raises UnsafePickle on anything unexpected.
  """
  # openpilot writes this with codecs.encode(..., "base64"), which wraps at 76 chars.
  # Strip the wrapping, then still validate the payload strictly.
  raw = base64.b64decode("".join(encoded.split()), validate=True)
  if len(raw) > MAX_PICKLE_BYTES:
    raise UnsafePickle(f"pickle too large: {len(raw)} bytes")

  obj = _SliceOnlyUnpickler(io.BytesIO(raw)).load()

  if not isinstance(obj, dict):
    raise UnsafePickle(f"expected dict, got {type(obj).__name__}")
  if len(obj) > MAX_SLICES:
    raise UnsafePickle(f"too many slices: {len(obj)}")

  out: dict[str, list[int | None]] = {}
  for key, value in obj.items():
    if not isinstance(key, str):
      raise UnsafePickle(f"non-str key {type(key).__name__}")
    if not isinstance(value, slice):
      raise UnsafePickle(f"non-slice value for {key!r}")
    for part in (value.start, value.stop, value.step):
      if part is not None and not isinstance(part, int):
        raise UnsafePickle(f"non-int slice bound for {key!r}")
    out[key] = [value.start, value.stop, value.step]
  return out


# --- protobuf wire format -------------------------------------------------------------------
# Only what ONNX needs. memoryview throughout so a 296MB model isn't copied per field.

_VARINT, _I64, _LEN, _I32 = 0, 1, 2, 5


def _varint(buf: memoryview, i: int) -> tuple[int, int]:
  result = shift = 0
  while True:
    byte = buf[i]
    i += 1
    result |= (byte & 0x7F) << shift
    if not byte & 0x80:
      return result, i
    shift += 7
    if shift > 70:
      raise ValueError("varint too long")


def _fields(buf: memoryview) -> Iterator[tuple[int, int, Any]]:
  """Yield (field_number, wire_type, value) for one protobuf message."""
  i, end = 0, len(buf)
  while i < end:
    key, i = _varint(buf, i)
    field, wire = key >> 3, key & 7
    if wire == _VARINT:
      value, i = _varint(buf, i)
    elif wire == _LEN:
      length, i = _varint(buf, i)
      if i + length > end:
        raise ValueError("length-delimited field overruns message")
      value, i = buf[i:i + length], i + length
    elif wire == _I64:
      value, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
    elif wire == _I32:
      value, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
    else:
      raise ValueError(f"unsupported wire type {wire}")
    yield field, wire, value


def _text(buf: memoryview) -> str:
  return bytes(buf).decode("utf-8", "replace")


class ValueInfo(NamedTuple):
  name: str
  shape: list[int | str]   # str for symbolic dims, preserved rather than zeroed
  dtype: str


def _dim(buf: memoryview) -> int | str:
  for field, _, value in _fields(buf):
    if field == 1:      # dim_value
      return value
    if field == 2:      # dim_param — symbolic, e.g. "batch"
      return _text(value)
  return 0


def _value_info(buf: memoryview) -> ValueInfo:
  name, shape, dtype = "", [], "unknown"
  for field, _, value in _fields(buf):
    if field == 1:
      name = _text(value)
    elif field == 2:                                  # TypeProto
      for tfield, _, tvalue in _fields(value):
        if tfield == 1:                               # tensor_type
          for efield, _, evalue in _fields(tvalue):
            if efield == 1:                           # elem_type
              dtype = _DTYPES.get(evalue, f"enum:{evalue}")
            elif efield == 2:                         # shape
              shape = [_dim(d) for f, _, d in _fields(evalue) if f == 1]
  return ValueInfo(name, shape, dtype)


def parse(path: str) -> dict[str, Any]:
  """Extract the descriptive record for one ONNX file.

  Structural only. Nothing here establishes that a model is safe or interchangeable — see
  AGENTS.md. Column semantics inside a slice, MDN field ordering, and input normalization are
  all invisible at this level.
  """
  with open(path, "rb") as handle:
    data = memoryview(handle.read())

  inputs: list[ValueInfo] = []
  outputs: list[ValueInfo] = []
  meta: dict[str, str] = {}
  opsets: dict[str, int] = {}
  operators: set[str] = set()

  for field, _, value in _fields(data):
    if field == 7:                                    # graph
      for gfield, _, gvalue in _fields(value):
        if gfield == 11:
          inputs.append(_value_info(gvalue))
        elif gfield == 12:
          outputs.append(_value_info(gvalue))
        elif gfield == 1:                             # node
          op_type, domain = "", ""
          for nfield, _, nvalue in _fields(gvalue):
            if nfield == 4:
              op_type = _text(nvalue)
            elif nfield == 7:
              domain = _text(nvalue)
          if op_type:
            operators.add(f"{domain}::{op_type}" if domain else op_type)
    elif field == 8:                                  # opset_import
      domain, version = "", 0
      for ofield, _, ovalue in _fields(value):
        if ofield == 1:
          domain = _text(ovalue)
        elif ofield == 2:
          version = ovalue
      opsets[domain or "ai.onnx"] = version
    elif field == 14:                                 # metadata_props
      key = val = ""
      for mfield, _, mvalue in _fields(value):
        if mfield == 1:
          key = _text(mvalue)
        elif mfield == 2:
          val = _text(mvalue)
      if key:
        meta[key] = val

  record: dict[str, Any] = {
    "input_shapes": {v.name: v.shape for v in inputs},
    "input_dtypes": {v.name: v.dtype for v in inputs},
    "output_shapes": {v.name: v.shape for v in outputs},
    "output_dtypes": {v.name: v.dtype for v in outputs},
    "opsets": opsets,
    "operators": sorted(operators),
    "model_checkpoint": meta.get("model_checkpoint"),
    "output_slices": None,
    "output_slices_error": None,
  }

  if (encoded := meta.get("output_slices")) is not None:
    try:
      record["output_slices"] = loads_output_slices(encoded)
    except Exception as exc:
      # Never fail the whole index over one hostile or malformed model.
      record["output_slices_error"] = f"{type(exc).__name__}: {exc}"

  return record
