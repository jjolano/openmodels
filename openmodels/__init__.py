"""Dependency-free OpenModels client. Server and inference packages are optional."""
from .contracts import ContractError, Manifest, Recipe
from .client import Catalog, DownloadCancelled, ModelStore, Package
from .selection import ModelSwitcher

__all__ = ["Catalog", "ContractError", "DownloadCancelled", "Manifest", "ModelStore", "ModelSwitcher", "Package", "Recipe"]
