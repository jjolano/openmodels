"""Optional execution contract. Implementations receive local, verified packages."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, TypedDict

from .client import Package
from .contracts import Manifest, Recipe

Inputs = TypeVar("Inputs", contravariant=True)
Outputs = TypeVar("Outputs", covariant=True)


class ExecutionTarget(TypedDict):
  backend: str
  hardware: str
  os: str
  runtime: str
  options: dict


@dataclass(frozen=True)
class PreparedBuild:
  manifest: Manifest
  path: Path
  recipe: Recipe


class Session(Protocol[Inputs, Outputs]):
  """Synchronous borrowed inputs; owned outputs; no network, scheduling or activation.

  A profile defines warm-up (None), timing, state and output meanings. Failed steps must
  invalidate state and must never return a previous result. Close is idempotent.
  """
  def step(self, inputs: Inputs) -> Outputs | None: ...
  def reset(self) -> None: ...
  def close(self) -> None: ...


class Runner(Protocol[Inputs, Outputs]):
  def prepare(self, package: Package, target: ExecutionTarget, *, timeout: int = 3600) -> PreparedBuild: ...
  def open(self, build: PreparedBuild) -> Session[Inputs, Outputs]: ...
