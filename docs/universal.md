# Universal contracts and integration

The dependency-free `openmodels` package is the catalog and installation interface.
`openmodels-runner-tinygrad` is an optional executable implementation. Neither exposes
activation, model selection in a consumer, nor an actuation interface.

## Documents and identity

Schema-1 snapshots contain `generated_at`, `sources`, `entries`, `documents`, `locations`,
and `evidence`. `entries` supply discovery names and source occurrence history. Names may resolve to several recipes; callers must select an exact recipe
digest in that case. Status stays on each occurrence.

`documents` maps full lowercase SHA-256 identifiers to **exact UTF-8 JSON document
strings**. This preserves document bytes inside an offline snapshot. The same bytes are
served at `/manifests/{digest}.json` and `/v1/manifests/{digest}`. Hash the UTF-8 string
bytes, not the JSON encoding of the outer string and not a reserialized object.

Each document has `schema: 1` and one `type`:

| Type | Content bound by its identity |
| --- | --- |
| `profile` | Namespaced versioned name, permitted slot sets, connections, required settings, I/O semantics, state behavior, source revision |
| `recipe` | Profile digest, role-to-member mapping, selected source context and configuration |
| `build` | Recipe digest, installed runner identity and code digest, complete target, compiled artifact digests |

Members include artifact SHA-256/size/format, source attribution, per-member configuration,
missing source fields, ports, recorded hardware targets, and data-only metadata.
Ports carry dimensions, dtype, and an optional semantic identifier. Matching dimensions
does not establish matching semantics. Unknown semantics are preserved explicitly.

The Python composer serializes new documents with sorted keys, compact separators,
ASCII escaping and finite JSON numbers (`openmodels.contracts.dumps`). This is deterministic
for this implementation, **not a claim of RFC 8785 canonicalization**. Other languages
verify/preserve the original document bytes. They may create their own valid documents;
different byte encodings have different identities even when their JSON values agree.
Document strings therefore travel in API composition responses as well as exported snapshots.

Recipes bind execution settings; display names, mirror URLs, later provenance discoveries,
and qualification evidence live outside that identity. Historical import creates a recipe
for each recorded source context rather than silently choosing an occurrence.

Limits: manifests are at most 2 MiB, snapshots at most 64 MiB, JSON nesting at most 48
levels, composition at most 32 slots. Duplicate keys, nonfinite numbers, unknown contract
fields and malformed document digests are rejected. The published JSON Schemas describe
the wire types; byte identity, graph references and operational limits are checked by the SDK.

## SDK and offline operation

```python
from pathlib import Path
from openmodels import Catalog, ModelStore

# A URL can point to /catalog.json or /v1/catalog. A local exported recipe is also a snapshot.
catalog = Catalog.load("https://models.example/catalog.json", expected_sha256=approved_revision)
page = catalog.search(kind="driving", offset=0, limit=100)
recipe = catalog.resolve(page["entries"][0]["recipe"])
package = ModelStore(Path("model-store"), catalog).fetch(recipe)
package.verify()
```

`expected_sha256` is optional. Without it, the caller trusts its configured HTTPS origin;
the SDK still verifies every included document and artifact digest. A hash proves byte
identity, not publisher authenticity or driving qualification. Catalog fetching never
silently substitutes a stale cache. Record/review a new catalog revision using consumer policy.

`search()` pages over the complete loaded snapshot, so fetching a catalog through the API
does not truncate it to the first page. An offline snapshot can use absolute download URLs;
for a self-hosted snapshot with relative blob paths, pass `base_url="https://models.example/"`.

Downloads use unique staging directories, exact declared sizes and SHA-256, then rename
the whole package into place. Existing packages are verified before reuse. An interrupted
or corrupt download never becomes installed. There is intentionally no `set_active()`.
Verified artifacts are cached by digest and hard-linked into recipes, so changing a source
context or reusing an encoder does not download or duplicate its bytes. A failed package
download may leave complete verified cache entries. Treat installed artifacts as immutable;
verification rejects local modifications before reuse or preparation.

Composition accepts contextual members, not bare weight hashes:

```python
recipe, report = catalog.compose(
    profile_id,
    {"vision": {"recipe": vision_recipe_id, "slot": "vision"},
     "on_policy": {"recipe": policy_recipe_id, "slot": "on_policy"}},
    configuration={"frame_skip": 4},
)
Path("my-recipe.json").write_text(catalog.export(recipe))
```

The profile defines legal slots and connections. The composer refuses known structural
contradictions and emits findings for unknown structure or semantics. Only unanimous
recorded settings become defaults. Explicit overrides are included in recipe identity;
unresolved required values block the optional runner's preparation. Per-member source
configuration remains available after an override. A composed result is never marked
attested; evidence of an upstream pairing remains a separate statement.

The equivalent `POST /v1/compose` body is `{profile, selection, configuration?}`. Its result
contains `id`, parsed `recipe`, `report`, and a self-contained `snapshot`. Redeem an exported
snapshot with `Catalog.load(path).resolve(id)`. Manifests use full SHA-256 digests.

The SDK also ships a small CLI:

```bash
python -m openmodels --catalog catalog.json list
python -m openmodels --catalog catalog.json export RECIPE_ID > selected.json
python -m openmodels --catalog catalog.json compose selection.json > composed.json
python -m openmodels --catalog catalog.json fetch RECIPE_ID --store ./models
```

## API, publication and directory

`GET /v1/catalog` provides the complete snapshot. `/v1/models` supports query, publisher,
kind, include_archive, offset and limit, returning model groups in `models`.
Archive inclusion defaults to true in both HTTP and `Catalog.models()`; false excludes
historical entries, independently of whether they have a published name. Names and aliases
are searchable; `name_kind` distinguishes published, source-derived and generated labels.
`/v1/models/{identity}` returns one group with exact recipe variants.
`Catalog.search()` remains the recipe-level query with profile filtering. `/v1/profiles`, `/v1/manifests/{digest}`,
`/v1/artifacts/{digest}`, `/v1/status`, and `/v1/schemas/{name}` expose the remaining data.
FastAPI publishes the HTTP description at `/openapi.json` and `/docs`.

`python web/render.py` publishes the universal catalog, digest-addressed snapshots,
manifests, schemas, standalone recipe exports, `models.json`, a complete `index.html` directory,
individual model pages, `archive.html`, `integrate.html`, and `compose.html`. Third-party manifests enter through reviewed files in
`publishers/<publisher>/*.json`; see [submission instructions](../publishers/README.md).

The directory supports browsing and downloads without JavaScript. Its generic browser
composer sends requests to the same Python composer through the API; it does not implement
a second set of validation rules. Self-hosted local deployments enable it automatically.
Static-only mirrors offer SDK composition.
`OPENMODELS_API_BASE` enables a separately hosted API; that API must allow the site's origin
with `OPENMODELS_CORS_ORIGINS` (comma-separated exact origins). Browser composition sends the
catalog digest in `If-Match`; a different API snapshot returns HTTP 412 with a refresh message. No catalog data controls this origin.

The API reads `/data/public/catalog.json`; the publisher writes referenced manifests before
atomically replacing this discovery snapshot. Historical blobs and existing manifests remain
append-only. An independent publication command is available:

```bash
python -m index.registry --index data/index.json --out data/public --publishers publishers
```

## Optional QCOM runner

Install the runner separately. It pins Tinygrad by Git revision and vendors attributed
preprocessing, temporal queue operations, serialization and decoding from openpilot
`555f48c5d28709f039b79f3f6105e51305edd4b5`. Importing the base SDK never imports these modules.

```bash
python -m pip install .
python -m pip install ./runners/tinygrad
```

The first supported execution profile is `comma/driving-supercombo-555f48c5/v1` and the exact
ordinary stock artifact `659727c4…f8009b`. The reviewed `Stock supercombo (555f48c5)` recipe
binds that profile and source constants. Its download locations come from the existing
archive publisher; if that archive has not mirrored the artifact, fetching reports that fact.
It is an implementation/reference package, **not a qualification record**.

```python
from openmodels_runner_tinygrad import Runner
from openmodels.profiles import TINYGRAD_REVISION

recipe = catalog.resolve("Stock supercombo (555f48c5)")
package = ModelStore("models", catalog).fetch(recipe)
runner = Runner("builds")
target = {"backend": "QCOM", "hardware": "comma3x", "os": exact_os_build,
          "runtime": f"tinygrad@{TINYGRAD_REVISION}",
          "options": {"camera_width": 1928, "camera_height": 1208, "deadline_ms": 50}}
build = runner.prepare(package, target)  # on device, outside scheduled inference
# The consumer qualifies and admits this exact build before opening/scheduling it.
with runner.open(build) as session:
    result = session.step(driving_inputs)
```

Initialize the consumer with `DEV=QCOM WARP_DEV=QCOM` before importing Tinygrad. Preparation
runs the pinned compiler in a subprocess. It never invokes code named by a manifest.
Only locally prepared, hash-verified executable artifacts in the caller-owned build store
are deserialized. Do not populate that store with third-party pickles. The build binds the
runner/SDK/metadata-parser/Tinygrad source files, Python and NumPy version and explicit target. Changing these
requires preparation again. The target's OS/build identity is supplied and verified by the host.

`DrivingInputs`, `Frame`, `Prediction`, and `DrivingOutput` are defined in
`openmodels_runner_tinygrad.session`. Frame buffers are borrowed synchronously and must
contain the complete 4,804,608-byte pinned NV12 allocation. Input arrays are Float32:
two 3×3 inverse warp transforms, an eight-element desire vector, and `[1,2]` traffic convention
and action delays. Frame IDs and monotonic timestamps come from the consumer.
The generic `Runner`/`Session` protocols, `ExecutionTarget`, and `PreparedBuild` types are
available from `openmodels.runner` without importing inference dependencies.

Opening warms the executable and resets queues. `step()` executes each consecutive pair;
the first four results are withheld while image history fills. Outputs are owned arrays,
with explicit prediction means and standard deviations and unchanged raw logits. The
runner rejects nonfinite values, malformed inputs, camera skew over 10ms, frame gaps/reorder,
cadence outside 25–75ms, and elapsed execution over 50ms. Failed steps reset state and return
no prior output. `reset()` discards recurrent/desire/image history; `close()` is idempotent.
This profile has no prepare-only/skip-evaluation mode. A host skipping input must reset.

Scheduling, camera buffers, synchronization, calibration acquisition, message mapping,
host control constants, qualification, activation, fallback and rollback remain consumer-owned.
The synchronous implementation copies frame buffers; target measurements must establish
whether qualified external-buffer imports are needed. No CPU backend or alternate model
profile is silently substituted.

Moonpilot can continue consuming catalog proposals outside its critical path and implement
the same profile natively. Nothing in this change changes its proposal/activation authority.
Openpilot and sunnypilot can opt into the Python session or keep their own model runners;
the contract does not imply compatibility with all of their model generations.

## Verification

```bash
python -m pip install '.[server,test]' numpy
python -m pip install --no-deps ./runners/tinygrad
python -m unittest discover -s tests -v
```

The ordinary suite covers identity/configuration, third-party ingestion, schema checks,
HTTP/offline parity, pagination, failed downloads, and the session lifecycle using a fake
executable. `tests/check_reference.py` additionally compares vendored function definitions
against a pinned checkout, checks real CPU preprocessing/temporal primitives, and optionally
checks the retained stock arithmetic output. It does not compile a driving model on CPU.

QCOM numerical parity, full recorded-sequence replay, memory use and deadline performance
must be measured on the device before a consumer can qualify this first runner. Host tests
are not hardware evidence.

Publication, archive checkpoints, durable snapshot URLs and API synchronization are described
in [CI and deployment](deployment.md).
