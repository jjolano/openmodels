"""Offline/HTTP catalog, pure composition, and a verified store. No activation API."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from .contracts import ContractError, Manifest, Recipe, SCHEMAS, dumps, loads, sha256, validate

MAX_SNAPSHOT = 64 * 1024 * 1024


def read_url(url, limit, timeout=30):
  if urlparse(url).scheme not in ("http", "https"):
    raise ContractError("only HTTP(S) catalog URLs are allowed")
  with urlopen(url, timeout=timeout) as response:
    raw = response.read(limit + 1)
  if len(raw) > limit:
    raise ContractError("response exceeds size limit")
  return raw


class Catalog:
  def __init__(self, raw, *, base_url=""):
    self.raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    self._data = loads(raw, MAX_SNAPSHOT)
    validate(self._data, SCHEMAS["snapshot"])
    self.base_url = base_url
    self.revision = sha256(raw.encode() if isinstance(raw, str) else raw)
    self._documents = {}
    artifacts = {}
    for digest, document in self._data["documents"].items():
      manifest = Manifest(document)
      if manifest.id != digest:
        raise ContractError("snapshot manifest digest mismatch")
      self._documents[digest] = manifest
      if manifest.data["type"] == "recipe":
        for member in manifest.data["members"].values():
          artifact = member["artifact"]
          previous = artifacts.setdefault(artifact["sha256"], artifact)
          if previous != artifact:
            raise ContractError("conflicting descriptions of the same artifact")
    for entry in self._data["entries"]:
      self.resolve(entry["recipe"])
    # Imported standalone recipes are checked even if they have no browse entry.
    for manifest in self._documents.values():
      if manifest.data["type"] == "recipe":
        self.resolve(manifest.id)

  @classmethod
  def load(cls, source, *, expected_sha256=None, base_url=""):
    """Load an explicit snapshot file or URL; no silent stale-cache fallback."""
    source = str(source)
    remote = urlparse(source).scheme in ("http", "https")
    if remote:
      raw = read_url(source, MAX_SNAPSHOT)
    else:
      with Path(source).open("rb") as handle:
        raw = handle.read(MAX_SNAPSHOT + 1)
    if expected_sha256 is not None and sha256(raw) != expected_sha256:
      raise ContractError("catalog revision digest mismatch")
    return cls(raw, base_url=base_url or (source if remote else ""))

  @property
  def data(self):
    return loads(dumps(self._data), MAX_SNAPSHOT)

  def manifest(self, digest):
    try:
      return self._documents[digest]
    except KeyError as exc:
      raise ContractError(f"unknown manifest: {digest}") from exc

  def search(self, *, query="", publisher=None, kind=None, profile=None, offset=0, limit=100):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
      raise ContractError("invalid pagination")
    entries = [e for e in self._data["entries"]
               if (not query or query.casefold() in e["name"].casefold())
               and (publisher is None or e["publisher"] == publisher)
               and (kind is None or e["kind"] == kind)
               and (profile is None or self.manifest(e["recipe"]).data["profile"] == profile)]
    return {"revision": self.revision, "generated_at": self._data["generated_at"],
            "total": len(entries), "offset": offset, "entries": loads(dumps(entries[offset:offset + limit]))}

  def models(self, *, include_archive=False, query="", kind=None, publisher=None):
    from .models import models
    return models(self, include_archive=include_archive, query=query, kind=kind, publisher=publisher)

  def model(self, identity):
    for model in self.models(include_archive=True):
      if model["id"] == identity:
        return model
    raise ContractError(f"unknown model: {identity}")

  def resolve(self, reference):
    if not isinstance(reference, str) or not reference:
      raise ContractError("recipe reference must be a nonempty string")
    if reference not in self._documents:
      choices = {e["recipe"] for e in self._data["entries"]
                 if reference == e["name"]}
      if len(choices) != 1:
        raise ContractError(f"reference resolves to {len(choices)} recipes; select a full recipe digest")
      reference = choices.pop()
    manifest = self.manifest(reference)
    if manifest.data["type"] != "recipe":
      raise ContractError("reference is not a recipe")
    return Recipe(manifest, self.manifest(manifest.data["profile"]))

  def compose(self, profile, selection, *, configuration=None):
    request = {"profile": profile, "selection": selection}
    if configuration is not None:
      request["configuration"] = configuration
    validate(request, SCHEMAS["compose"])
    members = {}
    for role, choice in sorted(selection.items()):
      source = self.resolve(choice["recipe"]).data
      if choice["slot"] != role:
        raise ContractError("a component cannot be relabelled as another role")
      try:
        members[role] = source["members"][choice["slot"]]
      except KeyError as exc:
        raise ContractError("unknown source slot") from exc
    # Only unanimous recorded values become defaults. Explicit choices stay in the recipe ID.
    configs = [m["configuration"] for m in members.values()]
    agreed = {k: v for k, v in configs[0].items()
              if v is not None and all(k in c and dumps(c[k]) == dumps(v) for c in configs[1:])}
    agreed.update(configuration or {})
    recipe = Recipe(Manifest.create({"schema": 1, "type": "recipe", "profile": profile,
                                    "members": members, "configuration": agreed}), self.manifest(profile))
    report = recipe.check()
    selected = {m["artifact"]["sha256"] for m in members.values()}
    report["evidence"] = [e for e in self._data["evidence"] if set(e["artifacts"]) <= selected]
    report["upstream_pairing"] = any(e["kind"] == "upstream_pairing" and set(e["artifacts"]) == selected
                                     for e in report["evidence"])
    report["composition_attested"] = False
    if len(members) > 1 and not report["upstream_pairing"]:
      report["findings"].append({"code": "cross_lineage_or_unknown", "detail": "No upstream pairing recorded"})
    return recipe, report

  def export(self, recipe):
    """A self-contained snapshot: profile, recipe, locations, and relevant evidence."""
    selected = {m["artifact"]["sha256"] for m in recipe.data["members"].values()}
    return dumps({"schema": 1, "generated_at": self._data["generated_at"], "sources": self._data["sources"],
                  "documents": {recipe.id: recipe.manifest.raw, recipe.profile.id: recipe.profile.raw},
                  "entries": [], "locations": {k: v for k, v in self._data["locations"].items() if k in selected},
                  "evidence": [e for e in self._data["evidence"] if set(e["artifacts"]) <= selected]})


@dataclass(frozen=True)
class Package:
  recipe: Recipe
  path: Path

  def artifact(self, role):
    return self.path / self.recipe.data["members"][role]["artifact"]["sha256"]

  def verify(self):
    if (self.path / "recipe.json").read_bytes() != self.recipe.manifest.raw.encode():
      raise ContractError("installed recipe changed")
    if (self.path / "profile.json").read_bytes() != self.recipe.profile.raw.encode():
      raise ContractError("installed profile changed")
    for role, member in self.recipe.data["members"].items():
      verify_file(self.artifact(role), member["artifact"])


def verify_file(path, artifact):
  with Path(path).open("rb") as handle:
    if Path(path).stat().st_size != artifact["size"] or hashlib.file_digest(handle, "sha256").hexdigest() != artifact["sha256"]:
      raise ContractError("artifact size or digest mismatch")


class DownloadCancelled(Exception):
  """The consumer cancelled an installation; no partial package is installed."""


def check_cancelled(cancelled):
  if cancelled is not None and cancelled():
    raise DownloadCancelled("download cancelled")


class ModelStore:
  def __init__(self, root, catalog):
    self.root, self.catalog = Path(root), catalog

  def fetch(self, recipe, *, on_progress=None, cancelled=None):
    """Stage a whole package and rename atomically. Existing packages are reverified."""
    check_cancelled(cancelled)
    self.root.mkdir(parents=True, exist_ok=True)
    final = self.root / recipe.id
    package = Package(recipe, final)
    if final.exists():
      package.verify()
      check_cancelled(cancelled)
      return package
    staging = Path(tempfile.mkdtemp(prefix=".download-", dir=self.root))
    try:
      for member in recipe.data["members"].values():
        check_cancelled(cancelled)
        artifact = member["artifact"]
        destination = staging / artifact["sha256"]
        if destination.exists():
          continue
        cached = self.root / ".artifacts" / artifact["sha256"]
        if cached.exists():
          verify_file(cached, artifact)
          os.link(cached, destination)
          continue
        location = self.catalog._data["locations"].get(artifact["sha256"], {})
        if location.get("availability") != "available" or not location.get("urls"):
          raise ContractError(f"artifact {location.get('availability', 'unknown')}: {artifact['sha256']}")
        failure = None
        for url in location["urls"]:
          check_cancelled(cancelled)
          try:
            url = urljoin(self.catalog.base_url, url)
            if urlparse(url).scheme not in ("https", "http"):
              raise ContractError("artifact URL needs an HTTP(S) origin")
            digest, received = hashlib.sha256(), 0
            with urlopen(url, timeout=30) as response, destination.open("wb") as handle:
              while True:
                check_cancelled(cancelled)
                chunk = response.read(min(1024 * 1024, artifact["size"] - received + 1))
                check_cancelled(cancelled)
                if not chunk:
                  break
                received += len(chunk)
                if received > artifact["size"]:
                  raise ContractError("artifact exceeds declared size")
                digest.update(chunk)
                handle.write(chunk)
                if on_progress:
                  on_progress(artifact["sha256"], received, artifact["size"])
                check_cancelled(cancelled)
            if received != artifact["size"] or digest.hexdigest() != artifact["sha256"]:
              raise ContractError("artifact size or digest mismatch")
            failure = None
            break
          except (OSError, ValueError) as exc:
            failure = exc
            destination.unlink(missing_ok=True)
        if failure:
          raise ContractError(f"artifact download failed: {failure}") from failure
        check_cancelled(cancelled)
        cached.parent.mkdir(exist_ok=True)
        try:
          os.link(destination, cached)
        except FileExistsError:
          verify_file(cached, artifact)
      (staging / "recipe.json").write_text(recipe.manifest.raw, encoding="utf-8")
      (staging / "profile.json").write_text(recipe.profile.raw, encoding="utf-8")
      Package(recipe, staging).verify()
      check_cancelled(cancelled)
      try:
        staging.rename(final)
      except OSError:
        if not final.exists():
          raise
        package.verify()  # Another complete concurrent install may have won the rename.
      package.verify()
      return package
    finally:
      shutil.rmtree(staging, ignore_errors=True)
