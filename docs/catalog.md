# Catalog contracts and integration

This repository does two things: it keeps an append-only archive of comma
(`commaai/openpilot`) models, and it precompiles the pinned stock model for comma 3X. The
`openmodels` package is the client: a dependency-free reader for the published catalog and a
verified downloader. It exposes no activation, scheduling or qualification.

## Documents and identity

Schema-1 snapshots contain `generated_at`, `sources`, `entries`, `documents`, `locations`
and `evidence`. `entries` supply discovery names and source occurrence history. A name may
resolve to several recipes; callers must select an exact recipe digest in that case. Status
stays on each occurrence.

`documents` maps full lowercase SHA-256 identifiers to **exact UTF-8 JSON document
strings**. This preserves document bytes inside an offline snapshot. The same bytes are served
at `/manifests/{digest}.json`. Hash the UTF-8 string bytes, not the JSON encoding of the outer
string and not a reserialized object.

Each document has `schema: 1` and one `type`:

| Type | Content bound by its identity |
| --- | --- |
| `profile` | Namespaced versioned name, permitted slot sets, connections, required settings, I/O semantics, state behavior, source revision |
| `recipe` | Profile digest, role-to-member mapping, selected source context and configuration |
| `build` | Recipe digest, runner identity, code digest, complete target, compiled artifact digests |

Members include artifact SHA-256/size/format, source attribution, per-member configuration,
missing source fields, ports, recorded hardware targets and data-only metadata. Ports carry
dimensions, dtype and an optional semantic identifier. Matching dimensions does not establish
matching semantics; unknown semantics are preserved explicitly.

The Python writer serializes with sorted keys, compact separators, ASCII escaping and finite
JSON numbers (`openmodels.contracts.dumps`). This is deterministic for this implementation,
**not a claim of RFC 8785 canonicalization**. Other languages verify and preserve the original
document bytes; a different encoding is a different identity even when the JSON values agree.

Recipes bind execution settings. Display names, mirror URLs, later provenance discoveries and
qualification evidence live outside that identity. Import creates one recipe per recorded
source context rather than silently choosing an occurrence.

Limits: manifests are at most 2 MiB, snapshots at most 64 MiB, JSON nesting at most 48 levels.
Duplicate keys, nonfinite numbers, unknown contract fields and malformed document digests are
rejected. The published JSON Schemas describe the wire types; byte identity, graph references
and operational limits are checked by the SDK.

## What the catalog contains

`generated_at` and `sources` record one observed upstream head. `entries` are the browse
surface: a name, the publisher (`commaai`, or `openmodels` for the pinned recipe), the kind,
the recipe digest, the source occurrences and a display model group.

`entries[].model` groups configurations. `model_class` (`standard`, `big`, `unknown`) comes
from the recorded importer variant; missing class stays unknown and is never inferred from
AMD/QCOM targets. Each variant's `hardware` list contains reviewed source attributions
(`name`, `url`, `method: "source"`) that appear only when both the recipe's source context and
its complete role/artifact digest set match a reviewed record in
[`index/model_hardware.json`](../index/model_hardware.json). Those records note upstream use,
not device qualification. Discovery fields never alter recipe identity.

`locations` maps each artifact digest to its recorded URLs and an `availability` of
`available`, `pending` or `gone`. Availability is a fact about the mirror state, not a
statement about the artifact. `evidence` records upstream pairings: co-occurrence in a
source tree, never qualification of a composed recipe.

`index/stock-supercombo.json` is the pinned stock recipe for the archived supercombo artifact
`659727c4…f8009b` (`555f48c5`). It is merged into every published snapshot after the archive
conversion, so its recipe and profile digests are fixed and the merged bytes are stable.

## SDK and CLI

```python
from pathlib import Path
from openmodels import Catalog, ModelStore

catalog = Catalog.load("https://jjolano.github.io/openmodels/catalog.json", expected_sha256=approved_revision)
page = catalog.search(kind="driving", offset=0, limit=100)
recipe = catalog.resolve(page["entries"][0]["recipe"])
package = ModelStore(Path("model-store"), catalog).fetch(recipe)
package.verify()
```

`expected_sha256` is optional. Without it, the caller trusts its configured HTTPS origin; the
SDK still verifies every included document and artifact digest. A hash proves byte identity,
not publisher authenticity or driving qualification. Catalog fetching never silently
substitutes a stale cache.

`search()` pages over the complete loaded snapshot. `models()` returns display groups;
`resolve()` accepts a recipe digest, or a name that resolves to exactly one recipe.
`export()` emits a self-contained snapshot for one recipe. An offline snapshot may use
absolute download URLs; for relative blob paths, pass `base_url="https://models.example/"`.

Downloads use unique staging directories, exact declared sizes and SHA-256, then rename the
whole package into place. Existing packages are verified before reuse, and an interrupted or
corrupt download never becomes installed. `on_progress(digest, received, total)` reports
progress; `cancelled()` raises `DownloadCancelled` and no partial package is installed. There
is intentionally no `set_active()`. Verified artifacts are cached by digest and hard-linked
into recipes, so reusing an encoder neither downloads nor duplicates its bytes. Treat
installed artifacts as immutable: verification rejects local modifications before reuse.

```bash
python -m openmodels --catalog catalog.json list --query supercombo
python -m openmodels --catalog catalog.json export RECIPE_ID > selected.json
python -m openmodels --catalog catalog.json fetch RECIPE_ID --store ./models
```

## Publication guarantees

- **Blobs are append-only.** Model ONNX blobs land in `blobs-NNNN` Releases and existing assets
  are never re-uploaded, so a run that dies halfway is safe to repeat.
- **Snapshots are retained.** Every published catalog is kept once as
  `catalog-DIGEST/DIGEST.json`, without clobbering existing assets, and each deployment is kept
  as `deployment-SHA256.json`. The snapshot embeds exact manifest strings, so an older catalog
  stays usable after its former Pages paths disappear.
- **Importer state is checkpointed.** Discoveries are saved to the `archive-state` branch before
  mirroring and before deployment, validated against the previous checkpoint so a stale writer
  is rejected rather than dropping archived provenance. Rollback changes deployment, never
  archive history.
- **Writers are separate from readers.** Only archive and Release writers hold
  `contents: write`; pull-request jobs hold no publishing credentials. Pages deploys from a
  tested artifact.

## Precompiled a630 builds

Off-device compilation targets comma 3X (Qualcomm a630) from the pinned recipe and the exact
ordinary stock artifact. It runs on Linux x86-64 with `qemu-user-static`, LLVM, and Tinygrad at
the revision in `openmodels.profiles`:

```bash
python -m ci.builds compile --catalog https://jjolano.github.io/openmodels/catalog.json \
  --store ./models --work ./builds --no-upload
```

Builds are published to the `builds-0001` Release as digest-addressed assets, and the site
renders them from an aggregate `builds.json`:

| Asset | Content |
| --- | --- |
| `model-<build_id>.pkl` | The compiled Tinygrad object; the `model.pkl` of a compile run |
| `build-<build_id>.json` | One build record (shape below) |
| `builds.json` | `{schema, generated_at, builds[]}`, sorted by `build_id` |

`build_id` is the first 16 hex characters of the digest over the pinned recipe document, the
source artifact digest, the compiler implementation digest and the pinned toolchain digest, so
identical inputs always name the same asset and a changed input produces a new one. The
implementation digest covers the compiler modules, the SDK, the Tinygrad source tree, Python,
NumPy **and the host LLVM the CPU backend loaded**, because the same pinned revision, model and
toolchain yields a different program set under a different LLVM. A same-name record is compared
byte-for-byte and a difference raises rather than overwriting. `builds.json` is the only mutable
asset and is replaced only when its bytes differ, which makes a repeated run upload nothing.

```json
{"schema":1,"build_id":"…16","inputs":"…64","recipe":"…64",
 "source":{"sha256":"659727c4…","size":60881999},
 "target":{"backend":"QCOM","hardware":"comma3x","os":"unvalidated","runtime":"tinygrad@138fb4a…",
           "options":{"camera_width":1928,"camera_height":1208,"deadline_ms":50}},
 "artifact":{"name":"model-<build_id>.pkl","url":"https://github.com/<repo>/releases/download/builds-0001/model-<build_id>.pkl",
             "sha256":"…","size":123},
 "compiler":{"implementation":"…","toolchain_sha256":"a9e8aa32…","cross_compiler_sha256":"…",
             "host_target":"CPU:LLVM…","python":"3.12.x","host_llvm":"/usr/lib/…/libLLVM-18.so sha256:…",
             "programs":77,"kernel_bytes":651024},
 "checks":["weighted-matmul-oracle","policy-and-warp-seeded-replay","pickle-round-trip","target-program-inspection"],
 "gpu_validated":false,"device_validated":false,"created_at":"…"}
```

A record is evidence of compilation, nothing more. The artifact was produced on x86-64 by
executing each original kernel through a CPU LLVM shim while the a630 programs were captured;
the shim and its original-kernel map stay outside the pickle. `checks` names the host-side
oracles that ran: a weighted matrix multiplication against NumPy, seeded policy/warp replay,
serialization round-trip, and inspection that the pickle contains exclusively compiled a630
programs and no host buffers.

`gpu_validated` and `device_validated` stay false. GPU numerical parity, target OS/runtime
compatibility, memory use and timing need device evidence, which this repository does not
produce. Consumers own selection, qualification, activation, scheduling and rollback.

## Licensing

The archived model files are copied byte-for-byte from `commaai/openpilot` and remain the
copyright of Comma.ai, Inc., distributed under openpilot's MIT license. The vendored
compiler tooling in `ci/qcom/` is derived from openpilot at
`555f48c5d28709f039b79f3f6105e51305edd4b5` under the same license, and compiled builds are
derived works of those models. See [third-party notices](../THIRD_PARTY_NOTICES.md).
