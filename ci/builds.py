"""Publish off-device a630 builds alongside the archive.

Compilation is deterministic input-to-artifact: the build identity hashes the pinned recipe
document, the source artifact, the compiler stack and the exact target, so the same inputs
always name the same release asset. Inputs that change produce a new build rather than
overwriting an existing one, and `builds.json` is the aggregate sidecar the site renders.

`gpu_validated` and `device_validated` stay false here: this tooling proves compilation only.
Device evidence is a separate claim and is not made by any record this module writes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from openmodels import Catalog, ContractError, ModelStore
from openmodels.contracts import dumps, loads, sha256

ROOT = Path(__file__).resolve().parent.parent
RELEASE_TAG = "builds-0001"
PINNED = ROOT / "index" / "stock-supercombo.json"
NOTICE = ROOT / "THIRD_PARTY_NOTICES.md"
CROSS_COMPILER = Path(__file__).resolve().parent / "qcom" / "compile.py"
DEFAULT_CATALOG = "https://jjolano.github.io/openmodels/catalog.json"
DEFAULT_REPO = "jjolano/openmodels"
# The exact checks the compiler reported. Named here so the record shape is one definition.
CHECKS = ["weighted-matmul-oracle", "policy-and-warp-seeded-replay", "pickle-round-trip",
          "target-program-inspection"]
EPOCH = "1970-01-01T00:00:00Z"


def stock_recipe_id():
  """The digest of the pinned recipe document, so compilation never depends on a display name."""
  documents = loads(PINNED.read_bytes())["documents"]
  return next(digest for digest, raw in documents.items() if loads(raw)["type"] == "recipe")


def build_id(recipe, source_sha256, implementation, toolchain_sha256, target):
  """The (inputs digest, build id) pair binding every input that can change the artifact."""
  inputs = sha256(dumps({"recipe": recipe, "source_sha256": source_sha256, "implementation": implementation,
                         "toolchain_sha256": toolchain_sha256, "target": target}).encode())
  return inputs, inputs[:16]


def needs_compile(asset_names, build_id):
  return f"build-{build_id}.json" not in asset_names


def manifest(records):
  """Deterministic aggregate: identical records always serialize to identical bytes.

  `generated_at` is the newest record rather than "now", so a repeated run uploads nothing.
  """
  records = sorted(records, key=lambda record: record["build_id"])
  return dumps({"schema": 1, "generated_at": max((r["created_at"] for r in records), default=EPOCH),
                "builds": records})


def load_builds(path):
  """Build records from a manifest file, for the site and for tests."""
  return [] if path is None else loads(Path(path).read_bytes())["builds"]


def compile_build(catalog_url, store, work, *, repo=""):
  """Compile the pinned stock recipe for a630 into `work`; returns (record, compiled).

  `compiled` is False when `work` already holds this exact build, which is what makes a
  repeated run cheap and silent about uploads.
  """
  if platform.system() != "Linux" or platform.machine() != "x86_64" or not shutil.which("qemu-aarch64-static"):
    raise ContractError("off-device compilation requires Linux x86-64 with qemu-aarch64-static")
  from ci.qcom.compile import TARGET, compile_package, host_llvm, implementation_digest
  from ci.qcom.toolchain import TOOLCHAIN_SHA256, check_bridge, install_bridge, inspect_artifact
  from openmodels.profiles import STOCK_SHA256

  repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
  # Set these before importing Tinygrad; no timing-based tuning or image allocation emulation.
  os.environ.update(DEV="QCOM;CPU:LLVM", WARP_DEV="QCOM", BEAM="0", IMAGE="0", JIT_BATCH_SIZE="0",
                    PYTHONDONTWRITEBYTECODE="1")
  implementation = implementation_digest()  # Enforces the installed Tinygrad Git pin.
  import tinygrad
  # The ARM Python in the upstream compiler sysroot must import this exact Tinygrad.
  os.environ["PYTHONPATH"] = str(Path(tinygrad.__file__).parent.parent)

  recipe_id = stock_recipe_id()
  inputs, identity = build_id(recipe_id, STOCK_SHA256, implementation, TOOLCHAIN_SHA256, TARGET)
  work = Path(work)
  work.mkdir(parents=True, exist_ok=True)
  record_path = work / f"build-{identity}.json"
  if record_path.is_file():
    return loads(record_path.read_bytes()), False

  catalog = Catalog.load(catalog_url)
  package = ModelStore(store, catalog).fetch(catalog.resolve(recipe_id))
  install_bridge()
  from tinygrad.device import Device
  compiler = Device["QCOM"].compiler
  try:
    check_bridge()
    with tempfile.TemporaryDirectory(prefix=".build-", dir=work) as tmp:
      staged = Path(tmp)
      (staged / "target.json").write_text(dumps(TARGET))
      compile_package(package.path, staged, staged / "target.json")
      artifact = staged / "model.pkl"
      with artifact.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
      record = {"schema": 1, "build_id": identity, "inputs": inputs, "recipe": recipe_id,
                "source": {"sha256": STOCK_SHA256, "size": package.recipe.data["members"]["supercombo"]["artifact"]["size"]},
                "target": TARGET,
                "artifact": {"name": f"model-{identity}.pkl",
                             "url": f"https://github.com/{repo}/releases/download/{RELEASE_TAG}/model-{identity}.pkl",
                             "sha256": digest, "size": artifact.stat().st_size},
                "compiler": {"implementation": implementation, "toolchain_sha256": TOOLCHAIN_SHA256,
                             "cross_compiler_sha256": hashlib.sha256(CROSS_COMPILER.read_bytes()).hexdigest(),
                             "host_target": str(Device["CPU"].renderer.target), "python": sys.version,
                             "host_llvm": " sha256:".join(host_llvm()),
                             **inspect_artifact(artifact)},
                "checks": CHECKS, "gpu_validated": False, "device_validated": False,
                "created_at": now()}
      os.replace(artifact, work / "model.pkl")
      (work / "target.json").write_text(dumps(TARGET) + "\n")
      (work / "report.json").write_text(dumps(record) + "\n")
      record_path.write_text(dumps(record) + "\n")
      return record, True
  finally:
    # Stop ARM Python before its sysroot TemporaryDirectory is cleaned up.
    process = compiler.compiler_process
    process.kill()
    process.wait()


def now():
  """One UTC spelling for records and manifests, so identical inputs stay comparable."""
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh(*args):
  if shutil.which("gh") is None:
    raise ContractError("the gh CLI is required to publish builds")
  result = subprocess.run(["gh", *args], capture_output=True, text=True)
  if result.returncode != 0:
    raise ContractError(f"gh {' '.join(args)}: {result.stderr.strip()}")
  return result.stdout


def gh_ok(*args):
  """Judged by exit code: `gh api` writes its error body to stdout on a 404."""
  if shutil.which("gh") is None:
    raise ContractError("the gh CLI is required to publish builds")
  return subprocess.run(["gh", *args], capture_output=True, text=True).returncode == 0


def ensure_release(repo):
  if gh_ok("api", f"repos/{repo}/releases/tags/{RELEASE_TAG}"):
    return
  gh("release", "create", RELEASE_TAG, "--repo", repo, "--title", "Off-device a630 builds",
     "--notes-file", str(NOTICE))


def asset_names(repo):
  return gh("api", f"repos/{repo}/releases/tags/{RELEASE_TAG}", "--jq", ".assets[].name").splitlines()


def download_manifest(repo, out, expected_sha256=None, optional=False):
  """Fetch the aggregate `builds.json`; `optional` writes an empty manifest when absent."""
  out = Path(out)
  out.parent.mkdir(parents=True, exist_ok=True)
  if gh_ok("api", f"repos/{repo}/releases/tags/{RELEASE_TAG}"):
    with tempfile.TemporaryDirectory() as tmp:
      fetched = subprocess.run(["gh", "release", "download", RELEASE_TAG, "--repo", repo,
                                "--pattern", "builds.json", "--dir", tmp],
                               capture_output=True, text=True)
      asset = Path(tmp) / "builds.json"
      if fetched.returncode == 0 and asset.is_file():
        raw = asset.read_bytes()
        if expected_sha256 and sha256(raw) != expected_sha256:
          raise ContractError("builds manifest digest mismatch")
        out.write_bytes(raw)
        return loads(raw)
  if not optional:
    raise ContractError(f"{repo} has no {RELEASE_TAG}/builds.json")
  out.write_text(manifest([]))
  return loads(out.read_bytes())


def upload(repo, record, work):
  """Publish the artifact, its record, and the refreshed aggregate manifest.

  Release assets are digest-addressed and never overwritten: a same-name record is compared
  byte-for-byte and a difference is an error rather than a clobber. `builds.json` is the one
  mutable index, so it is replaced only when its bytes actually differ.
  """
  work = Path(work)
  identity = record["build_id"]
  record_name = f"build-{identity}.json"
  ensure_release(repo)
  present = asset_names(repo)
  with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    if record_name in present:
      gh("release", "download", RELEASE_TAG, "--repo", repo, "--pattern", record_name, "--dir", str(tmp))
      if (tmp / record_name).read_bytes() != (work / record_name).read_bytes():
        raise ContractError(f"published build record differs: {record_name}")
    else:
      staged = tmp / record_name
      shutil.copyfile(work / record_name, staged)
      gh("release", "upload", RELEASE_TAG, str(staged), "--repo", repo)
    if record["artifact"]["name"] not in present:
      staged = tmp / record["artifact"]["name"]
      shutil.copyfile(work / "model.pkl", staged)
      gh("release", "upload", RELEASE_TAG, str(staged), "--repo", repo)
    manifest_path = tmp / "builds.json"
    download_manifest(repo, manifest_path, optional=True)
    records = {item["build_id"]: item for item in loads(manifest_path.read_bytes())["builds"]}
    records[identity] = record
    wanted = manifest(records.values())
    if wanted != manifest_path.read_text():
      manifest_path.write_text(wanted)
      gh("release", "upload", RELEASE_TAG, str(manifest_path), "--repo", repo, "--clobber")
  return record


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  compile_cmd = commands.add_parser("compile", help="compile the pinned stock recipe for a630")
  compile_cmd.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
  compile_cmd.add_argument("--catalog", default=os.environ.get("OPENMODELS_CATALOG", DEFAULT_CATALOG))
  compile_cmd.add_argument("--store", type=Path, default=Path("data/models"))
  compile_cmd.add_argument("--work", type=Path, default=Path("data/builds"))
  compile_cmd.add_argument("--no-upload", action="store_true")
  manifest_cmd = commands.add_parser("manifest", help="download the aggregate builds manifest")
  manifest_cmd.add_argument("--repo", required=True)
  manifest_cmd.add_argument("--out", type=Path, required=True)
  manifest_cmd.add_argument("--sha256")
  manifest_cmd.add_argument("--optional", action="store_true")
  args = parser.parse_args()
  if args.command == "compile":
    record, compiled = compile_build(args.catalog, args.store, args.work, repo=args.repo)
    print(dumps(record) if compiled else f"unchanged build {record['build_id']}")
    if args.no_upload:
      (args.work / "builds.json").write_text(manifest([record]))
    else:
      upload(args.repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO, record, args.work)
    return
  download_manifest(args.repo, args.out, args.sha256, optional=args.optional)


if __name__ == "__main__":
  main()
