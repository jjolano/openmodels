#!/usr/bin/env python3
"""Self-check for the ONNX metadata parser. Run: python3 index/test_metadata.py

Fixtures are synthetic ModelProtos, not truncated real models: `output_slices` lives at the
*tail* of a real file (byte 46,876,945 of 46,877,473 in driving_vision.onnx), so a prefix
truncation loses exactly the thing under test.
"""

import base64
import codecs
import os
import pickle
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from index.metadata import UnsafePickle, loads_output_slices, parse  # noqa: E402


# --- minimal protobuf encoder, just enough to build fixtures --------------------------------

def _varint(n):
  out = bytearray()
  while True:
    byte = n & 0x7F
    n >>= 7
    out.append(byte | (0x80 if n else 0))
    if not n:
      return bytes(out)


def _tag(field, wire):
  return _varint((field << 3) | wire)


def _len_field(field, payload):
  return _tag(field, 2) + _varint(len(payload)) + payload


def _varint_field(field, value):
  return _tag(field, 0) + _varint(value)


def _value_info(name, dims, elem_type=1):
  """ValueInfoProto: 1=name, 2=TypeProto{1=tensor{1=elem_type, 2=shape{1=dim...}}}"""
  shape = b"".join(
    _len_field(1, _varint_field(1, d) if isinstance(d, int) else _len_field(2, d.encode()))
    for d in dims
  )
  tensor = _varint_field(1, elem_type) + _len_field(2, shape)
  return _len_field(1, name.encode()) + _len_field(2, _len_field(1, tensor))


def build_model(inputs, outputs, metadata=None, opset=18, ops=("Conv", "Relu")):
  """Assemble a minimal but structurally valid ModelProto."""
  nodes = b"".join(_len_field(1, _len_field(4, op.encode())) for op in ops)
  graph = nodes
  graph += b"".join(_len_field(11, _value_info(n, d)) for n, d in inputs)
  graph += b"".join(_len_field(12, _value_info(n, d)) for n, d in outputs)

  model = _len_field(8, _len_field(1, b"") + _varint_field(2, opset))
  model += _len_field(7, graph)
  for key, value in (metadata or {}).items():
    model += _len_field(14, _len_field(1, key.encode()) + _len_field(2, value.encode()))
  return model


def write_fixture(data):
  handle = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
  handle.write(data)
  handle.close()
  return handle.name


def encode_slices(mapping):
  """Encode like openpilot does: base64(pickle({name: slice})) — get_model_metadata.py:38."""
  pickled = pickle.dumps({k: slice(*v) for k, v in mapping.items()})
  return codecs.encode(pickled, "base64").decode()


# --- the exploit this parser exists to stop -------------------------------------------------

class _Evil:
  """Reduces to os.system. Only dangerous if something calls plain pickle.loads on it."""

  def __reduce__(self):
    return (os.system, ("touch /tmp/openmodels_pwned",))


def test_refuses_malicious_pickle():
  payload = codecs.encode(pickle.dumps(_Evil()), "base64").decode()
  marker = "/tmp/openmodels_pwned"
  if os.path.exists(marker):
    os.unlink(marker)

  try:
    loads_output_slices(payload)
    raise AssertionError("SECURITY: malicious pickle was accepted")
  except UnsafePickle as exc:
    assert "os.system" in str(exc) or "system" in str(exc), f"unexpected refusal: {exc}"

  assert not os.path.exists(marker), "SECURITY: payload executed despite refusal"


def test_refuses_oversized_pickle():
  huge = codecs.encode(pickle.dumps({"x" * 100: slice(0, 1)} | {str(i): slice(i, i + 1) for i in range(200_000)}), "base64").decode()
  try:
    loads_output_slices(huge)
    raise AssertionError("oversized pickle was accepted")
  except UnsafePickle:
    pass


def test_decodes_real_shaped_slices():
  # Mirrors driving_supercombo.onnx, including the negative-start `pad` entry.
  mapping = {"meta": (0, 55, None), "plan": (1576, 2566, None), "pad": (-2, None, None)}
  decoded = loads_output_slices(encode_slices(mapping))
  assert decoded["plan"] == [1576, 2566, None], decoded["plan"]
  assert decoded["pad"] == [-2, None, None], decoded["pad"]


# --- parser behaviour -----------------------------------------------------------------------

def test_parses_split_vision_shape():
  """Split-era vision: image inputs only, and no plan/lead/lane_lines slices."""
  slices = {"meta": (0, 55, None), "pose": (87, 99, None), "hidden_state": (117, 629, None)}
  path = write_fixture(build_model(
    inputs=[("img", [1, 12, 128, 256]), ("big_img", [1, 12, 128, 256])],
    outputs=[("outputs", [1, 632])],
    metadata={"output_slices": encode_slices(slices), "model_checkpoint": "4eaea7d4/200"},
  ))
  record = parse(path)
  os.unlink(path)

  assert record["input_shapes"] == {"img": [1, 12, 128, 256], "big_img": [1, 12, 128, 256]}
  assert record["input_dtypes"]["img"] == "float32"
  assert record["output_shapes"] == {"outputs": [1, 632]}
  assert record["model_checkpoint"] == "4eaea7d4/200"
  assert "plan" not in record["output_slices"], "vision-only model must not expose plan"
  assert record["opsets"] == {"ai.onnx": 18}
  assert record["operators"] == ["Conv", "Relu"]


def test_parses_combined_supercombo_shape():
  slices = {"meta": (0, 55, None), "plan": (1576, 2566, None), "lane_lines": (117, 645, None)}
  path = write_fixture(build_model(
    inputs=[("img", [1, 12, 128, 256]), ("features_buffer", [1, 24, 512]),
            ("desire_pulse", [1, 25, 8])],
    outputs=[("outputs", [1, 2576])],
    metadata={"output_slices": encode_slices(slices)},
  ))
  record = parse(path)
  os.unlink(path)

  assert record["input_shapes"]["features_buffer"] == [1, 24, 512]
  assert "plan" in record["output_slices"]
  assert record["output_slices"]["plan"] == [1576, 2566, None]


def test_preserves_symbolic_dims():
  """Symbolic dims must survive as names — get_model_metadata.py collapses them to 0."""
  path = write_fixture(build_model(
    inputs=[("img", ["batch", 12, 128, 256])], outputs=[("outputs", [1, 632])],
  ))
  record = parse(path)
  os.unlink(path)
  assert record["input_shapes"]["img"] == ["batch", 12, 128, 256], record["input_shapes"]


def test_survives_hostile_slices_without_failing_the_file():
  """A bad output_slices records an error; it must not sink the whole record."""
  path = write_fixture(build_model(
    inputs=[("img", [1, 12, 128, 256])], outputs=[("outputs", [1, 632])],
    metadata={"output_slices": codecs.encode(pickle.dumps(_Evil()), "base64").decode()},
  ))
  record = parse(path)
  os.unlink(path)

  assert record["output_slices"] is None
  assert "UnsafePickle" in record["output_slices_error"], record["output_slices_error"]
  assert record["input_shapes"] == {"img": [1, 12, 128, 256]}, "rest of record must survive"


def test_handles_missing_metadata():
  path = write_fixture(build_model(
    inputs=[("img", [1, 12, 128, 256])], outputs=[("outputs", [1, 632])],
  ))
  record = parse(path)
  os.unlink(path)
  assert record["output_slices"] is None and record["output_slices_error"] is None
  assert record["model_checkpoint"] is None


if __name__ == "__main__":
  tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
  for test in tests:
    test()
    print(f"  ok  {test.__name__}")
  print(f"\n{len(tests)} passed")
