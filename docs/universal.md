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
Groups and variants expose `model_class` (`standard`, `big`, or `unknown`) separately
from each variant's execution `targets`. Archive class comes from the recorded importer
variant; missing class stays unknown, including publisher submissions. It is not inferred
from AMD/QCOM targets. These discovery fields do not alter recipe identity.
Each variant's `hardware` list contains reviewed source attributions (`name`, `url`,
`method: "source"`). A Chestnut attribution appears only when both the recipe's source
context and complete role/artifact digest set match a reviewed record in
[`index/model_hardware.json`](../index/model_hardware.json). It records upstream use,
not device qualification or compatibility with other source configurations. New releases
do not inherit this attribution automatically; adding a record requires checking their
immutable build source and exact artifact hashes.
`Catalog.search()` remains the recipe-level query with profile filtering. `/v1/profiles`, `/v1/manifests/{digest}`,
`/v1/artifacts/{digest}`, `/v1/status`, and `/v1/schemas/{name}` expose the remaining data.
FastAPI publishes the HTTP description at `/openapi.json` and `/docs`.

`python web/render.py` publishes the catalog, digest-addressed snapshots, manifests,
schemas, standalone recipe exports and `models.json`. The composition workbench is at
`index.html` (also `compose.html`); `models.html`, individual model pages, `archive.html`
and the Developers guide at `integrate.html` provide browsing and integration help.
Third-party manifests enter through reviewed files in `publishers/<publisher>/*.json`;
see [submission instructions](../publishers/README.md).

The model directory renders at most 30 compact rows per page. Static page links and downloads
work without JavaScript. Interactive search loads a small, digest-addressed discovery index
on demand, including aliases, and preserves query, type, naming filter and page in the URL.
A failed search load leaves static browsing available.

The workbench uses a separate digest-addressed component index and loads detailed recipe
exports only when selected. Profile-defined slots filter the component library, which renders
at most 30 results at once. Starting from a model configuration retains its contextual members
and settings. The inspector shows recorded and missing settings alongside explicit overrides.
The browser exports a **Composition request** (`{profile, selection, configuration?}`), plus
Python and CLI examples pinned to the displayed catalog snapshot. Browser checks cover form
completeness and JSON syntax; the SDK owns composition validation, findings and final recipe
identity. Explicit overrides enter recipe identity when the SDK composes the request.

The workbench needs no hosted API. `OPENMODELS_API_BASE` adds an API-reference link for
separately hosted deployments. Direct API consumers can send the quoted catalog digest in
`If-Match` with `POST /v1/compose`; a different API snapshot returns HTTP 412. For browser
consumers of that API, configure `OPENMODELS_CORS_ORIGINS` with exact allowed origins.

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

### Experimental off-device compilation

The stock model can also be compiled on Linux x86-64 with `qemu-aarch64-static`, LLVM,
and the separately installed pinned runner:

```bash
python -m ci.cross_compile --catalog https://jjolano.github.io/openmodels/catalog.json \
  --store ./models --out ./experimental-qcom
```

Run this from a source checkout; the output directory must not already exist. The command
pins and verifies the upstream ARM compiler archive, compiles a630 GPU binaries, and uses
CPU LLVM execution of each original kernel during capture to preserve weights and state. It checks
a weighted matrix multiplication against NumPy, then reuses the policy/warp seeded replay
and pickle round-trip checks. The final pickle contains QCOM programs; the CPU execution
shim and its original-kernel map remain outside it. Host replay requires that in-process map.

`model.pkl`, `target.json`, and `report.json` are experimental outputs, with hashes, compiler
identity and an explicit `gpu_validated: false`. No trusted runner receipt is created, so
`Runner.open()` does not accept this output. GPU numerical parity, compatibility with the
target OS/runtime, memory use and timing still need device evidence. The prototype uses
buffer allocations (`IMAGE=0`), disables timing-based tuning, and supports only this stock
profile on a630. Its printed timings measure host execution, not device performance.

Moonpilot can continue consuming catalog proposals outside its critical path and implement
the same profile natively. Nothing in this change changes its proposal/activation authority.
Openpilot and sunnypilot can opt into the Python session or keep their own model runners;
the contract does not imply compatibility with all of their model generations.

### Big-model quantization probe

`ci/quantize_probe.py` measures what weight-only int8 quantization of a catalog model yields, and
what the pinned Tinygrad revision then makes of it: it fetches one recipe through the ordinary
verified client, inventories the graph, quantizes Conv/Gemm/MatMul weights with ONNX Runtime, and
reports import, a630 compilation through the bridge above, and host numeric divergence.

```bash
cd openmodels
export PY=/var/tmp/openmodels-quant/venv/bin/python

$PY -m ci.quantize_probe \
  --recipe 08df19414744ee5135f24b76fa3d52eebf70db9346db1d8de12bb9fb2641653e \
  --stock-bytes 60881999 --out /var/tmp/openmodels-quant/out-chestnut

$PY -m ci.quantize_probe \
  --recipe ae8f9e0f6b6ee434453bac25adc8c1bde7202e1a9a35bc4da07c274b171a01d2 \
  --stock-bytes 60881999 --out /var/tmp/openmodels-quant/out-big195

$PY -m ci.quantize_probe --recipe "Stock supercombo (555f48c5)" \
  --stock-bytes 60881999 --out /var/tmp/openmodels-quant/out-stock
```

`--recipe` takes a digest, or a name that resolves to exactly one recipe; every phase writes only
under `--out`, and the QCOM phase needs the pinned Tinygrad and `qemu-aarch64-static` described
above. Results below are from catalog revision `c6fa4c5ae88e4373f98a927c2f8d1765ee3d70a0c39161a7b276213e51d3b561`.

| Subject | Source bytes | int8 bytes | int8/source | int8 ops added | Import | a630 (archived → int8) | rel_l2 | cosine |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Chestnut big `08df1941…` | 765950064 | 386407975 | 0.504 | MatMulInteger 153, ConvInteger 41, DynamicQuantizeLinear 185 | rejected, rejected | rejected, rejected | not measured | not measured |
| Big fused `ae8f9e0f…` | 195490097 | 100134689 | 0.512 | MatMulInteger 167, ConvInteger 79, DynamicQuantizeLinear 237 | ok, ok | rejected, rejected | not measured | not measured |
| Standard `555f48c5` | 60881999 | 30943511 | 0.508 | MatMulInteger 88, ConvInteger 60, DynamicQuantizeLinear 137 | ok, ok | 77 programs, 651024 bytes, 20.6 s → rejected | 0.283 | 0.987 |

The Chestnut-attributed model does not import in the pinned Tinygrad revision at all: it declares
opset domain `org.tinygrad` version 1 for its single `Contiguous` node, and `OnnxRunner` rejects any
domain it does not know with `ValueError: 'org.tinygrad' is not a valid Domain`. Its int8
derivative inherits that rejection, so it has no a630 or numeric result either; `MatMul` owns
713441280 of its 763846856 initializer bytes. Quantization still halves the bytes: 386407975 B,
0.504× the archived 765950064 B, but 6.35× the 60881999-byte standard artifact and 12.5× the
standard model's own int8 derivative of 30943511 B.

int8 changes bytes, not arithmetic. Weight-only dynamic quantization leaves every multiply in a
narrow integer type with no accelerator support: no renderer under `tinygrad/renderer/` lowers an
integer dot product (`dp4a` and `dot4` appear in none of them), and the probe performs no device
execution. Nothing here is a claim about FLOPs, latency or memory use on an a630.

Three escalations were needed before ONNX Runtime would quantize these artifacts at all, all
recorded in each report: the composed graphs declare the same `ai.onnx` opset four times
(`Failed to find proper ai.onnx domain`), their recorded `value_info` contradicts shape inference,
and quantizing the float16 graph directly yields a graph that is no longer valid ONNX
(`DynamicQuantizeLinear` applied to float16 activations). Every subject therefore reports
`path_used: "dynamic-fp32-rewrite"`. Inputs are also the runtime's, not the declaration's: the
graphs declare float16 for everything but the images, while `examples/openpilot/compile3.py` and the
pinned runner both feed float32 there, and no int8 derivative could be lowered with float16
activations at all.

The a630 column comes from the host bridge above, so its programs, bytes and seconds describe
compilation through qemu plus a CPU replay of every kernel, never hardware, and a rejected int8
entry means capture aborted before the kernels were compiled: the standard model fuses a 65-buffer
kernel and the big fused model a 74-buffer one, above the 63 arguments a CPU program accepts. That
ceiling belongs to the host shim, not to an a630. The pinned revision also could not execute the
archived 195 MB big fused model on the host at all (its rewritten sink fails UOp verification, and
with `SPEC=0` the rewriter raises `KeyError: Ops.AND`), so that subject has no reference output —
a finding about the revision, not about quantization.

Every number here is a host measurement with no device execution. No quantized artifact is
published and no qualification is claimed; the probe writes only under `--out`. A quantized
derivative is a new unqualified artifact rather than a reclassification of any archived model:
recipe identity, digests and model class are untouched. Divergence is measured against synthetic
inputs (seed 0) and, for the standard model, includes the fp32 rewrite of the archived graph, so
`rel_l2` and `cosine` are divergence indicators, not driving-quality results.

Fitting the richest model the hardware can run needs the archived graph to import and compile
before any of the above matters, so the same probe sweeps the big classes directly — `--phase
inventory --phase import --phase compile` measures the archived artifact when no int8 derivative
exists:

| Big-class artifact | Recipe | Bytes | Opset domains | Import | a630 build of the archived graph |
| --- | --- | --- | --- | --- | --- |
| Chestnut big `e8d82173…` | `08df1941…` | 765950064 | ai.onnx 20, org.tinygrad 1 | rejected | rejected |
| Chestnut big `f3669cb7…` | `9fef17c5…` | 765955335 | ai.onnx 20, org.tinygrad 1 | rejected | rejected |
| Big fused `471f3727…` | `ae8f9e0f…` | 195490097 | ai.onnx 20 | ok | rejected |
| Big `b45887db69…` | `19b23d2f…` | 1757297630 | ai.onnx 20 | ok | 82 programs, 664756 bytes, 87.7 s |
| Big `328fe4dd57…` | `41765f69…` | 1757328957 | ai.onnx 20 | ok | 76 programs, 710732 bytes, 90.2 s |

The richest class the pinned stack builds is the 1.75 GB one: 1754743469 initializer bytes against
the standard artifact's 60006795, and the same input interface as the stock profile (identical
input shapes), but a different decode layout — `output_slices` puts `lane_lines` at 0–528 and
`plan` at 917–1907 where the stock model has 117–645 and 1576–2566 — so admitting it means a new
profile, not a bigger stock one. Code size is not what limits it: 82 a630 programs and 664756
bytes against the standard model's 77 and 651024, for 28.9× the archived bytes. Its 88–90 s build
is qemu time, not device time.

Quantization does not shrink that class into something usable. ONNX Runtime emits 881049913 bytes
for `b45887db69…`, but that artifact carries the same illegal element types as the smaller models
(`DynamicQuantizeLinear` on float16 activations), and the fp32 working copy that would make it
legal doubles those weights to about 3.5 GB — past the 2 GiB single-file ONNX serialization limit,
which `onnx.save` reports as `EncodeError: Failed to serialize proto`. The archived artifact is
itself within 20 % of that ceiling, so the richest class is also the class that cannot be quantized
into a legal graph at all.

What blocks a richer model today is support rather than bytes: the Chestnut-attributed 766 MB
family is refused by a single `org.tinygrad` `Contiguous` node, the 195 MB big fused family is
refused during lowering, and the 1.75 GB family has no legal int8 derivative.

Support is not the only limit. Against the recorded deadline of 50 ms (`deadline_ms`, and
`cadence_hz: 20` in the stock profile), the measured work per inference and the vendor's own
numbers — 29.9 GB/s of LPDDR4X ([Snapdragon 845 specifications](https://www.anandtech.com/show/12420/snapdragon-845-performance-preview))
and an Adreno 630 of 256 FP32 ALUs at 710 MHz, 363.5 GFLOP/s ([Adreno](https://en.wikipedia.org/wiki/Adreno),
whose 512-ALU reading is 727.0 GFLOPS) — put every big class over budget before any inefficiency:

| Model class | Weights | MACs per inference | Weights streamed at 29.9 GB/s | Arithmetic at 363.5 GFLOP/s | at 727 GFLOP/s |
| --- | --- | --- | --- | --- | --- |
| Standard `555f48c5` | 60006795 | 1316239920 | 2.0 ms | 7.2 ms | 3.6 ms |
| Standard `555f48c5` + dmonitoring `db1dd2c9` | 69128507 | 2363929840 | 2.3 ms | 13.0 ms | 6.5 ms |
| Standard `555f48c5` + dmonitoring `2fccd3c9` | 76026203 | 2954405776 | 2.5 ms | 16.3 ms | 8.1 ms |
| Standard `555f48c5` twice | 120013590 | 2632479840 | 4.0 ms | 14.5 ms | 7.2 ms |
| Big fused `ae8f9e0f` | 191701073 | 12804142080 | 6.4 ms | 70.4 ms | 35.2 ms |
| Chestnut `08df1941` | 763846856 | 47736509440 | 25.5 ms | 262.6 ms | 131.3 ms |
| Big `19b23d2f` | 1754743469 | 101260979200 | 58.7 ms | 557.1 ms | 278.5 ms |

Two standard-class models fit where no big one does. The archived driver-monitoring models import
and are cheap — `db1dd2c9…` is 9121712 B of fp32 weights for 1047689920 MACs, `2fccd3c9…` is
16019408 B for 1638165856 — so pairing either with the standard supercombo costs 13.0–16.3 ms of
arithmetic at the conservative peak, 26–33 % of the 50 ms budget, and 2.3–2.5 ms of weight traffic;
two copies of the supercombo cost 14.5 ms. Every one of those is under the 12.8 GMAC of the
*smallest* big class, and none of the big classes can be built at all. Driver monitoring is refused
by the bridge at 89–97 buffers (host shim again, not a device limit), so its a630 build is
unmeasured rather than ruled out.

The 1.75 GB class cannot hold 20 Hz even with free arithmetic: its weights alone take 58.7 ms to
stream once per inference. On arithmetic the big classes need 35–557 ms at 100 % of peak, 0.7–11×
the budget, and real kernels run far below peak, so a big model is a lower-cadence model on this
GPU, not a drop-in one. That headroom, not the byte count, is what a second standard-class model
spends. int8 halves the weight traffic (Chestnut 12.8 ms, 1.75 GB 29.3 ms) and leaves the arithmetic
floor untouched, because nothing lowers an integer dot product — it helps a model fit, not run
faster.

These are floors derived from published specifications and measured graph structure, not device
measurements: no a630 was involved. Device memory, sustained clocks, thermal behavior, real kernel
efficiency, the GPU work the camera warps already take and the actual deadline still need device
evidence, and the a630 numbers above are qemu compilation plus host replay. A second model is also a
composition, not a switch: the stock profile pins one `supercombo` slot, so pairing is a new profile
with new members, and pairing records co-occurrence, never qualification.

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
