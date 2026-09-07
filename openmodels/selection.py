"""Synchronous model selection for consumers that own admission and activation."""
from copy import deepcopy

from .client import DownloadCancelled, ModelStore, Package, check_cancelled
from .contracts import ContractError


class ModelSwitcher:
  def __init__(self, catalog, root, *, support):
    """support(recipe) returns None to allow it, or a nonempty rejection reason."""
    if not callable(support):
      raise TypeError("an explicit consumer support policy is required")
    self.catalog, self.store, self.support = catalog, ModelStore(root, catalog), support
    self.package = None
    self.build = None
    self._status = {"state": "idle", "recipe": None, "error": None}

  @property
  def status(self):
    return dict(self._status)

  def _reason(self, recipe):
    reason = self.support(recipe)
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
      raise TypeError("support must return None or a nonempty rejection reason")
    return reason

  def _package(self, recipe):
    package = Package(recipe, self.store.root / recipe.id)
    package.verify()
    return package

  def _availability(self, recipe):
    for member in recipe.data["members"].values():
      location = self.catalog._data["locations"].get(member["artifact"]["sha256"], {})
      if location.get("availability") != "available" or not location.get("urls"):
        return "Model artifacts are not available for download"
    return None

  def list_models(self, *, include_unsupported=False):
    """Return named models and variants annotated for this consumer and store."""
    result = []
    for model in self.catalog.models(include_archive=True):
      record = deepcopy(model)
      variants = []
      for variant in record["variants"]:
        recipe = self.catalog.resolve(variant["recipe"])
        reason = self._reason(recipe)
        installed, installation_error = False, None
        if (self.store.root / recipe.id).exists():
          try:
            self._package(recipe)
            installed = True
          except (OSError, ContractError) as exc:
            installation_error = str(exc)
        reason = reason or (None if installed else self._availability(recipe))
        variant.update(supported=reason is None, reason=reason, installed=installed,
                       installation_error=installation_error)
        if include_unsupported or reason is None:
          variants.append(variant)
      if variants:
        record["variants"] = variants
        result.append(record)
    return result

  def _select(self, recipe_id):
    recipe = self.catalog.resolve(recipe_id)
    if recipe.id != recipe_id:
      raise ContractError("select an exact recipe digest")
    reason = self._reason(recipe)
    if reason:
      raise ContractError(reason)
    return recipe

  def install(self, recipe_id, *, on_progress=None, cancelled=None):
    """Download and verify; preserve the last successful package on failure."""
    self._status = {"state": "downloading", "recipe": recipe_id, "error": None}
    try:
      check_cancelled(cancelled)
      recipe = self._select(recipe_id)
      package = self.store.fetch(recipe, on_progress=on_progress, cancelled=cancelled)
    except Exception as exc:
      self._status.update(state="cancelled" if isinstance(exc, DownloadCancelled) else "failed", error=str(exc))
      raise
    self.package, self.build = package, None
    self._status.update(state="installed")
    return package

  def prepare(self, recipe_id, runner, target):
    """Prepare an installed package using an explicit runner; never activate it."""
    self._status = {"state": "preparing", "recipe": recipe_id, "error": None}
    try:
      recipe = self._select(recipe_id)
      package = self._package(recipe)
      build = runner.prepare(package, target)
      if build.recipe.id != recipe.id:
        raise ContractError("runner prepared a different recipe")
    except Exception as exc:
      self._status.update(state="failed", error=str(exc))
      raise
    self.package, self.build = package, build
    self._status.update(state="prepared")
    return build
