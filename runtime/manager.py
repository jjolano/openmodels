"""Catalog, download, and activation for building a model selector.

Everything a fork needs behind a model-picker UI, with no opinion about the UI itself: browse
and group the catalog, download with progress callbacks, verify, and track which bundle is
active. Roughly the surface of sunnypilot's ModelManagerSP, minus their cereal types.

Dependency-free (urllib + stdlib) so it drops into a fork without a package manager.

Two behaviours are not configurable, because getting them wrong is how a fork ships a model
that appears to work:
  - every downloaded file is verified against its oid, and a mismatch aborts;
  - host constants travel with the weights and are stored alongside them.

It flags rather than hides: withdrawn models and ones this fork cannot build are listed and
annotated, never silently dropped. A user who cannot see why a model is missing goes looking
for it somewhere less careful.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

CHUNK = 1 << 20

# Every status is listed by default. Withdrawn models are the reason this archive exists, so
# hiding them sends people to look somewhere less careful; they are surfaced with their status
# instead. A picker MUST render that status — see Catalog.status_of and is_withdrawn.
ALL_STATUSES = frozenset({"merged", "reverted", "pr_only"})
MERGED_ONLY = frozenset({"merged"})


class CatalogUnavailable(Exception):
  """Neither the API nor the static mirror could be reached."""


class VerificationError(Exception):
  """A downloaded file did not match its oid. Never proceed past this."""


@dataclass
class Progress:
  """Passed to the caller's callback; shaped for a progress bar."""
  filename: str
  file_index: int
  file_count: int
  done_bytes: int
  total_bytes: int
  started: float

  @property
  def fraction(self) -> float:
    return self.done_bytes / self.total_bytes if self.total_bytes else 0.0

  @property
  def eta_seconds(self) -> float | None:
    elapsed = time.monotonic() - self.started
    if elapsed <= 0 or self.done_bytes <= 0:
      return None
    rate = self.done_bytes / elapsed
    return (self.total_bytes - self.done_bytes) / rate if rate else None


ProgressFn = Callable[[Progress], None]


def _get(url: str, timeout: int = 20) -> bytes:
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()


@dataclass
class Catalog:
  """The model catalog, from the API with a static-mirror fallback."""
  data: dict[str, Any]
  source: str
  api: str

  @classmethod
  def load(cls, api: str, mirror: str | None = None, cache: Path | None = None,
           max_age: int = 3600) -> "Catalog":
    """API first, mirror second, on-disk cache last.

    The cache is a genuine third tier, not an optimisation: a device that is offroad and
    offline should still be able to show the user what it already knows about.
    """
    if cache and cache.exists() and (time.time() - cache.stat().st_mtime) < max_age:
      return cls(json.loads(cache.read_text()), "cache", api)

    failures = []
    sources = [(f"{api}/v1/models?limit=1000", "api")]
    if mirror:
      sources.append((f"{mirror}/index.json", "mirror"))
    for url, source in sources:
      try:
        data = json.loads(_get(url))
        if cache:
          cache.parent.mkdir(parents=True, exist_ok=True)
          cache.write_text(json.dumps(data))
        return cls(data, source, api)
      except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        failures.append(f"{source}: {exc}")

    if cache and cache.exists():
      return cls(json.loads(cache.read_text()), "stale-cache", api)
    raise CatalogUnavailable("; ".join(failures))

  @property
  def generated_at(self) -> str | None:
    return self.data.get("generated_at")

  @staticmethod
  def status_of(bundle: dict[str, Any]) -> str:
    if "status" in bundle:
      return bundle["status"]
    return max(bundle["occurrences"], key=lambda o: o["date"])["status"]

  @property
  def suspect_oids(self) -> frozenset[str]:
    return frozenset(f["oid"] for f in self.data.get("files", []) if f.get("suspect"))

  def list(self, *, kind: str | None = "driving", allow_statuses: Iterable[str] = ALL_STATUSES,
           family: str | None = None, variant: str | None = None,
           search: str | None = None, capabilities: Any = None,
           only_runnable: bool = False) -> list[dict[str, Any]]:
    """Annotated, newest first. **Flags rather than hides.**

    Every entry carries `status` and, when `capabilities` is given, a `compatibility` verdict —
    so a picker can grey out a model and say why instead of quietly omitting it. A user who
    cannot see why a model is missing goes looking for it somewhere less careful.

    `only_runnable=True` opts into actual filtering; the default lists everything.

    Entries are shallow copies, so repeated calls never accumulate annotations on the catalog.
    """
    allowed = frozenset(allow_statuses)
    out = []
    for bundle in self.data.get("bundles", []):
      if kind and bundle.get("kind") != kind:
        continue
      status = self.status_of(bundle)
      if status not in allowed:
        continue
      if family and bundle.get("family") != family:
        continue
      if variant and bundle.get("variant") != variant:
        continue
      if search and search.lower() not in f"{bundle.get('name','')} {bundle['bundle_id']}".lower():
        continue

      entry = dict(bundle, status=status, withdrawn=status != "merged")
      if capabilities is not None:
        from runtime.plan import check_compatibility
        verdict = check_compatibility(bundle, capabilities, self.suspect_oids)
        if only_runnable and not verdict.runnable:
          continue
        entry["compatibility"] = {
          "runnable": verdict.runnable,
          "blockers": verdict.blockers,
          "cautions": verdict.cautions,
        }
      out.append(entry)
    return sorted(out, key=lambda b: b["introduced_by"]["date"], reverse=True)

  def support_gaps(self, capabilities: Any, **kwargs) -> list[tuple[str, int, str]]:
    """What building support for each missing capability would unlock.

    Blockers aggregate into a roadmap: "adding the sunnypilot compiler unlocks 76 models" is a
    decision a maintainer can act on, where 76 individually unsupported models is just noise.
    Returns (code, model_count, example_detail), largest win first.
    """
    from runtime.plan import check_compatibility
    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    for bundle in self.list(capabilities=None, **kwargs):
      for blocker in check_compatibility(bundle, capabilities, self.suspect_oids).blockers:
        counts[blocker.code] = counts.get(blocker.code, 0) + 1
        examples.setdefault(blocker.code, blocker.detail)
    return sorted(((c, n, examples[c]) for c, n in counts.items()),
                  key=lambda row: row[1], reverse=True)

  def group_by_year(self, **kwargs) -> dict[str, list[dict[str, Any]]]:
    """Folder-style grouping for a picker UI."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for bundle in self.list(**kwargs):
      groups.setdefault(bundle["introduced_by"]["date"][:4], []).append(bundle)
    return dict(sorted(groups.items(), reverse=True))

  def provenance(self, bundle_id: str) -> dict[str, Any]:
    """Full record, including host constants. Requires the API (the mirror has no per-bundle
    endpoint), so fall back to the catalog entry when offline."""
    try:
      return json.loads(_get(f"{self.api}/v1/models/{bundle_id}/provenance"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
      for bundle in self.data.get("bundles", []):
        if bundle["bundle_id"] == bundle_id and "files" in bundle:
          return bundle
      raise CatalogUnavailable(f"provenance for {bundle_id}: {exc}") from exc


@dataclass
class ModelStore:
  """What is on disk, and which bundle is active."""
  root: Path
  _state: Path = field(init=False)

  def __post_init__(self) -> None:
    self.root = Path(self.root)
    self._state = self.root / "state.json"

  def _read(self) -> dict[str, Any]:
    if self._state.exists():
      try:
        return json.loads(self._state.read_text())
      except json.JSONDecodeError:
        pass
    return {"active": None, "installed": {}}

  def _write(self, state: dict[str, Any]) -> None:
    self.root.mkdir(parents=True, exist_ok=True)
    tmp = self._state.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(self._state)          # atomic: a torn state file bricks the picker

  def installed(self) -> dict[str, dict[str, Any]]:
    return self._read()["installed"]

  def active(self) -> str | None:
    return self._read()["active"]

  def path_for(self, bundle_id: str) -> Path:
    return self.root / bundle_id

  def is_installed(self, bundle_id: str) -> bool:
    """Installed means every file is present *and* still hashes correctly."""
    record = self.installed().get(bundle_id)
    if not record:
      return False
    base = self.path_for(bundle_id)
    return all((base / f["filename"]).exists() for f in record["files"])

  def record(self, bundle_id: str, provenance: dict[str, Any]) -> None:
    state = self._read()
    state["installed"][bundle_id] = {
      "files": [{"role": f["role"], "filename": f["filename"], "oid": f["oid"]}
                for f in provenance["files"]],
      "host_constants": provenance.get("host_constants", {}),
      "host_constants_missing": provenance.get("host_constants_missing", []),
      "frame_skip": provenance.get("frame_skip"),
      # A picker must be able to mark a composed model as unattested.
      "source": provenance.get("source", "upstream"),
      "attested": provenance.get("attested", True),
      "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    self._write(state)

  def set_active(self, bundle_id: str | None) -> None:
    if bundle_id is not None and not self.is_installed(bundle_id):
      raise FileNotFoundError(f"{bundle_id} is not installed")
    state = self._read()
    state["active"] = bundle_id
    self._write(state)

  def active_constants(self) -> dict[str, Any]:
    """The host constants that must be applied with the active model.

    Empty when nothing is active. A fork reads this at startup rather than hardcoding, since
    the values change with the model.
    """
    active = self.active()
    return self.installed().get(active, {}).get("host_constants", {}) if active else {}

  def remove(self, bundle_id: str) -> None:
    state = self._read()
    state["installed"].pop(bundle_id, None)
    if state["active"] == bundle_id:
      state["active"] = None
    self._write(state)
    shutil.rmtree(self.path_for(bundle_id), ignore_errors=True)


def download_bundle(catalog: Catalog, bundle_id: str, store: ModelStore,
                    on_progress: ProgressFn | None = None,
                    api: str | None = None) -> dict[str, Any]:
  """Download every file in a bundle, verifying each, and record it as installed.

  Files land in a temporary directory and are moved into place only once all of them verify,
  so an interrupted download can never leave a half-installed bundle that looks usable.
  """
  api = api or catalog.api
  record = catalog.provenance(bundle_id)
  files = record["files"]
  total = sum(f["size"] for f in files)

  dest = store.path_for(bundle_id)
  staging = dest.with_suffix(".partial")
  shutil.rmtree(staging, ignore_errors=True)
  staging.mkdir(parents=True, exist_ok=True)

  started = time.monotonic()
  done = 0
  try:
    for index, entry in enumerate(files):
      digest = hashlib.sha256()
      target = staging / entry["filename"]
      with urllib.request.urlopen(f"{api}/v1/files/{entry['oid']}/download", timeout=120) as r, \
           open(target, "wb") as handle:
        while chunk := r.read(CHUNK):
          digest.update(chunk)
          handle.write(chunk)
          done += len(chunk)
          if on_progress:
            on_progress(Progress(entry["filename"], index, len(files), done, total, started))
      if digest.hexdigest() != entry["oid"]:
        raise VerificationError(
          f"{entry['filename']}: sha256 {digest.hexdigest()} != oid {entry['oid']}"
        )

    shutil.rmtree(dest, ignore_errors=True)
    staging.replace(dest)
  finally:
    shutil.rmtree(staging, ignore_errors=True)

  store.record(bundle_id, record)
  return record
