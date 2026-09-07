"""Data-only, language-neutral contracts. IDs hash exact UTF-8 document bytes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

MAX_DOCUMENT = 2 * 1024 * 1024
DIGEST = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
DATA = {"type": "object"}


class ContractError(ValueError):
  pass


def obj(properties, required=None):
  return {"type": "object", "properties": properties,
          "required": list(properties) if required is None else required, "additionalProperties": False}


def array(items):
  return {"type": "array", "items": items, "maxItems": 10000}


ARTIFACT = obj({"sha256": DIGEST, "size": {"type": "integer", "minimum": 1}, "format": TEXT})
PORT = obj({"shape": array({"type": "integer", "minimum": 1}),
            "dtype": {"type": ["string", "null"]}, "semantics": {"type": ["string", "null"]}})
PORTS = {"type": "object", "additionalProperties": PORT}
MEMBER = obj({"artifact": ARTIFACT, "source": DATA, "configuration": DATA,
              "missing": array(TEXT), "inputs": PORTS, "outputs": PORTS,
              "targets": array(TEXT), "metadata": DATA})
TARGET = obj({"backend": TEXT, "hardware": TEXT, "os": TEXT, "runtime": TEXT, "options": DATA})
SCHEMAS = {
  "profile": obj({"schema": {"const": 1}, "type": {"const": "profile"}, "name": TEXT,
                  "slot_sets": array(array(TEXT)),
                  "connections": array(obj({"from": TEXT, "output": TEXT, "to": TEXT, "input": TEXT})),
                  "required_configuration": array(TEXT), "inputs": DATA, "outputs": DATA,
                  "state": DATA, "implementation_source": DATA}),
  "recipe": obj({"schema": {"const": 1}, "type": {"const": "recipe"}, "profile": DIGEST,
                 "members": {"type": "object", "minProperties": 1, "maxProperties": 32,
                             "additionalProperties": MEMBER}, "configuration": DATA}),
  "build": obj({"schema": {"const": 1}, "type": {"const": "build"}, "recipe": DIGEST,
                "runner": TEXT, "implementation": DIGEST, "target": TARGET,
                "artifacts": {"type": "object", "minProperties": 1, "additionalProperties": ARTIFACT}}),
  "compose": obj({"profile": DIGEST,
                  "selection": {"type": "object", "minProperties": 1, "maxProperties": 32,
                                "additionalProperties": obj({"recipe": DIGEST, "slot": TEXT})},
                  "configuration": DATA}, ["profile", "selection"]),
  "snapshot": obj({"schema": {"const": 1}, "generated_at": TEXT, "sources": DATA,
                   "documents": {"type": "object", "additionalProperties": {"type": "string"}},
                   "entries": array(obj({"name": TEXT, "publisher": TEXT, "kind": TEXT,
                                         "recipe": DIGEST, "occurrences": array(DATA),
                                         "model": obj({"id": TEXT, "name": TEXT, "family": TEXT,
                                                       "description": TEXT, "links": array(TEXT),
                                                       "archived": {"type": "boolean"}})},
                                        ["name", "publisher", "kind", "recipe", "occurrences"])),
                   "locations": {"type": "object", "additionalProperties": obj({
                     "urls": array(TEXT), "availability": {"enum": ["available", "pending", "gone"]}})},
                   "evidence": array(obj({"kind": {"enum": ["upstream_pairing", "publisher_semantics"]},
                                          "source": DATA, "artifacts": array(DIGEST)}))}),
}


def schema(name):
  return {"$schema": "https://json-schema.org/draft/2020-12/schema", **SCHEMAS[name]}


def validate(value, spec, path="$", depth=0):
  """Validate the JSON Schema subset used above, without server dependencies."""
  if depth > 48:
    raise ContractError(f"{path}: nesting limit exceeded")
  types = {"object": dict, "array": list, "string": str, "integer": int,
           "number": (int, float), "null": type(None), "boolean": bool}
  allowed = spec.get("type")
  if allowed:
    allowed = [allowed] if isinstance(allowed, str) else allowed
    if not any(isinstance(value, types[t]) and not (t in ("integer", "number") and isinstance(value, bool)) for t in allowed):
      raise ContractError(f"{path}: expected {allowed}")
  if "const" in spec and (value != spec["const"] or type(value) is not type(spec["const"])):
    raise ContractError(f"{path}: expected {spec['const']!r}")
  if "enum" in spec and value not in spec["enum"]:
    raise ContractError(f"{path}: unsupported value")
  if isinstance(value, dict):
    if not all(isinstance(k, str) for k in value):
      raise ContractError(f"{path}: keys must be strings")
    if not spec.get("minProperties", 0) <= len(value) <= spec.get("maxProperties", 10000):
      raise ContractError(f"{path}: invalid object size")
    for key in spec.get("required", []):
      if key not in value:
        raise ContractError(f"{path}: missing {key}")
    for key, item in value.items():
      child = spec.get("properties", {}).get(key, spec.get("additionalProperties", {}))
      if child is False:
        raise ContractError(f"{path}: unknown field {key}")
      validate(item, child if isinstance(child, dict) else {}, f"{path}.{key}", depth + 1)
  elif isinstance(value, list):
    if len(value) > spec.get("maxItems", 10000):
      raise ContractError(f"{path}: array too large")
    for i, item in enumerate(value):
      validate(item, spec.get("items", {}), f"{path}[{i}]", depth + 1)
  elif isinstance(value, str):
    if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 64 * 1024 * 1024):
      raise ContractError(f"{path}: invalid string length")
    if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
      raise ContractError(f"{path}: invalid format")
  elif isinstance(value, (int, float)) and not isinstance(value, bool):
    if not math.isfinite(value) or value < spec.get("minimum", -math.inf):
      raise ContractError(f"{path}: invalid number")
  elif value is not None and not isinstance(value, bool):
    raise ContractError(f"{path}: not JSON data")


def dumps(value):
  validate(value, {})
  return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def loads(raw, limit=MAX_DOCUMENT):
  if not isinstance(raw, (str, bytes)) or len(raw.encode() if isinstance(raw, str) else raw) > limit:
    raise ContractError("JSON document too large or not bytes/text")

  def pairs(items):
    result = {}
    for key, value in items:
      if key in result:
        raise ContractError(f"duplicate JSON key: {key}")
      result[key] = value
    return result

  try:
    value = json.loads(raw, object_pairs_hook=pairs)
    validate(value, {})
    return value
  except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError) as exc:
    raise ContractError(f"invalid JSON: {exc}") from exc


def sha256(raw: bytes):
  return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Manifest:
  raw: str

  def __post_init__(self):
    data = loads(self.raw)
    if not isinstance(data, dict) or data.get("type") not in ("recipe", "profile", "build"):
      raise ContractError("unknown manifest type")
    validate(data, SCHEMAS[data["type"]])

  @classmethod
  def create(cls, data):
    return cls(dumps(data))

  @property
  def id(self):
    return sha256(self.raw.encode("utf-8"))

  @property
  def data(self):
    # Return a fresh object: callers cannot mutate an already-identified manifest.
    return loads(self.raw)


@dataclass(frozen=True)
class Recipe:
  manifest: Manifest
  profile: Manifest

  def __post_init__(self):
    if self.manifest.data["type"] != "recipe" or self.profile.data["type"] != "profile":
      raise ContractError("expected recipe and profile")
    if self.manifest.data["profile"] != self.profile.id:
      raise ContractError("profile digest mismatch")
    self.check()

  @property
  def id(self):
    return self.manifest.id

  @property
  def data(self):
    return self.manifest.data

  def check(self, target=None):
    data, profile = self.data, self.profile.data
    members = data["members"]
    if set(members) not in [set(slots) for slots in profile["slot_sets"]]:
      raise ContractError("unusable role set for this profile")
    findings = []
    targets = [set(m["targets"]) for m in members.values() if m["targets"]]
    if targets and not set.intersection(*targets):
      raise ContractError("components have contradictory hardware targets")
    for edge in profile["connections"]:
      if edge["from"] not in members or edge["to"] not in members:
        continue
      a = members[edge["from"]]["outputs"].get(edge["output"])
      b = members[edge["to"]]["inputs"].get(edge["input"])
      if not a or not b:
        findings.append({"code": "structure_unknown", "connection": edge})
        continue
      if a["shape"] != b["shape"] or (a["dtype"] and b["dtype"] and a["dtype"] != b["dtype"]):
        raise ContractError("connection shape or dtype mismatch")
      if not a["semantics"] or not b["semantics"] or a["semantics"] != b["semantics"]:
        findings.append({"code": "semantics_unverified", "connection": edge})
    for key in profile["required_configuration"]:
      if key not in data["configuration"] or data["configuration"][key] is None:
        findings.append({"code": "configuration_unresolved", "key": key})
    if target is not None:
      validate(target, TARGET)
      if targets and target["backend"] not in set.intersection(*targets):
        findings.append({"code": "target_unsupported", "backend": target["backend"]})
    return {"findings": findings, "execution_support": "requires_runner_check", "qualification": "consumer_owned"}
