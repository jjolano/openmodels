# openmodels

A public archive of openpilot driving models, indexed automatically from `commaai/openpilot`
git history. The public catalog is static and every download links directly to its recorded
GitHub Release; the same data is also available through the self-hosted HTTP API.

Most interesting openpilot models are **reverted from master shortly after landing** — they
survive only at specific commit SHAs, and every fork that wants to offer model choice has to
rediscover them by hand. This indexes all of them, with the provenance needed to use one.

Live counts and mirror health are recorded in the published `index.json` and `/v1/status`; they
are not duplicated here because the archive changes every day.

## What it claims, and what it doesn't

Claims, all independently verifiable against `commaai/openpilot`:

- this blob has this sha256, byte-identical to what upstream carried
- it appeared at these commits, via this PR, with this status at each occurrence
- when it ran upstream, the host constants were *these*

**It does not claim that a model is safe, or that two models are interchangeable.** Two models
can share every tensor shape while meaning different things — column order inside a slice is
hard-coded in openpilot, not declared in the file. So compatibility is expressed as *provenance*
("this ran upstream at this commit with these constants") rather than inferred from structure.
There is deliberately no `/v1/compat` endpoint.

## API

```
GET  /v1/models                        ?kind= &family= &variant= &role= &status= &in_head=
GET  /v1/models/{bundle_id}            files, roles, occurrences
GET  /v1/models/{bundle_id}/provenance the compatibility surface
POST /v1/compose                       combine indexed halves into a bundle
GET  /v1/lineage/{checkpoint}          what this checkpoint shipped alongside
GET  /v1/files/{oid}/download          302 → release asset
GET  /v1/status                        freshness, counts, upstream head
GET  /docs                             OpenAPI explorer
```

Pull a model, verifying as you go:

```bash
python clients/reference.py --list
python clients/reference.py --pull <bundle_id> --out ./models
```

The reference client uses the static mirror by default, downloads from the release recorded on
each file, verifies every blob against its oid, and **shows each model's status** so withdrawn
ones are visible rather than hidden. Pass `--api https://your-instance` to use a self-hosted API
and `--merged-only` to require at least one merged occurrence. Status belongs to an occurrence,
so a bundle that was merged, reverted, and re-landed exposes all three facts rather than one
misleading summary.

## Integration library (`runtime/`)

Drop-in, dependency-free (stdlib only), and deliberately **outside the control path** — it never
runs inference or touches actuation. Two pieces:

**`runtime/manager.py`** — everything behind a model-picker UI, with no opinion about the UI:

```python
from pathlib import Path
from runtime.manager import Catalog, ModelStore, download_bundle

MIRROR = "https://jjolano.github.io/openmodels"
cat   = Catalog.load("", mirror=MIRROR, cache=Path("cache.json"))
years = cat.group_by_year(kind="driving")        # folder-style picker
store = ModelStore(Path("models"))

download_bundle(cat, bundle_id, store, on_progress=lambda p: bar(p.fraction, p.eta_seconds))
store.set_active(bundle_id)
store.active_constants()      # -> the constants to apply with this model
```

Two behaviours are not configurable, because getting them wrong ships a model that appears to
work: every file is verified against its oid, and downloads are staged then moved atomically so
an interrupted transfer can never look installed.

**It flags rather than hides.** Withdrawn models (reverted, PR-only) and models this fork cannot
mechanically build are both listed, annotated, and left for your UI to render — a user who cannot
see why a model is missing goes looking for it somewhere less careful. Pass `capabilities=` to
attach a verdict, `only_runnable=True` to actually filter, and `support_gaps()` to turn blockers
into a roadmap without baking today’s catalog counts into client code.

**`runtime/plan.py`** — works out *how* to compile a bundle, without compiling it:

```python
plan = plan_bundle(provenance, files, host_frame_skip=4)
plan.model_type   # vision_policy | supercombo | vision_multi_policy
plan.compiler     # "openpilot" or "sunnypilot" — upstream can't take a supercombo
plan.command(compiler_path, output, model_size, camera_resolutions)
```

It maps files to compiler flags by **role, not filename**, and duck-types input keys the way
sunnypilot does, so a 2021 model presenting `input_imgs`/`desire` plans as readily as a 2026 one
presenting `img`/`desire_pulse`. Disagreements (your `frame_skip` vs the model's) and absences
(host constants that never existed) surface as warnings rather than being silently resolved.

**`runtime/build.py`** — runs that compile **on the device**, with rails around it:

```python
result = build_and_activate(plan, compiler_path, store,
                            model_size="512x256", camera_resolutions=["1928x1208"])
```

Compile into staging → smoke-test the artifact → move it into place atomically → activate. Any
failure restores the previously active model, so a bad compile costs the user a wait rather than
a working car.

This is safe to automate because compilation is **fail-loud**: a compile either emits a pickle or
it does not, and the pickle either loads or it does not. The smoke test deliberately stops at
structural checks (size, unpickles, carries `metadata`) — running inference to "check" a model
would mean interpreting its outputs, which is the boundary this package does not cross.

Compilation must happen on-target: tinygrad's QCOM backend opens `/dev/kgsl-3d0` directly. We
verified that the CPU fallback cannot substitute — on the pinned tinygrad revision it fails
linking libm, reproducibly, in a clean container. So the plan is produced anywhere and executed
on the device.

## Combining models

comma ships one vision encoder against several policies, so mixing halves is how upstream already
works — and `model_checkpoint` records which halves were built for each other. A supercombo's
checkpoint is literally its vision and policy checkpoints concatenated.

```bash
curl -X POST "$API/v1/compose" -H 'content-type: application/json' \
  -d '{"vision": "<oid>", "on_policy": "<oid>"}'
# -> { "bundle_id": …, "code": "OM3-2PYP-MEKY-CXXA-2C", … }
```

Build one in the browser at **`/compose`** and get a code to paste into a model picker. Redeem it
with `GET /v1/compose/{code}`, or offline with `runtime.manager.redeem_code(code, catalog)`.

Codes are **14 characters** to type — sized for a car touchscreen, with the `OM3-` prefix
optional on input. The alphabet excludes every letter that resembles a digit
(`B G I L O Q S T U Z`), so a misread self-corrects: type `O` for `0` or `S` for `5` and the code
still resolves.

They carry *which files you picked*, not a promise about them: truncated oids resolved against
the catalog on redemption, where every check re-runs. A genuinely damaged code fails to resolve
rather than quietly naming different weights.

Stateless: `bundle_id` is derived from the members, so nothing is stored — the manifest returned
is the whole artifact, and it feeds `runtime/plan.py` like any provenance record.

A pairing recorded as having **shipped upstream** composes cleanly. One without recorded
attestation is returned with a cross-lineage caution rather than refused: the latent between
vision and policy is untyped, so a mismatched encoder loads, runs, and produces confident
nonsense. The failure is silent, so the warning isn't. Only a seam-width mismatch, an unusable
role set, or an unknown oid is rejected.

Everything from `/v1/compose` carries `attested: false`. It may be built from halves that shipped
together, but this exact combination has never been driven.

Host configuration remains per-half. If the same weights appeared upstream under more than one
host configuration, the response reports every context and leaves that role unresolved rather
than guessing.

## Using a model in a fork

The registry hands you source weights and provenance. Making them run is your fork's job:

1. Apply the `host_constants` from `/provenance` — `LAT_SMOOTH_SECONDS` and
   `LONG_SMOOTH_SECONDS` feed lateral delay, and comma changes them in the same commit that
   swaps a model. New weights against stale constants steer wrong while appearing to work.
2. Compile for your target. openpilot builds the tinygrad pickle from ONNX during scons; the
   QCOM backend opens `/dev/kgsl-3d0`, so a device-usable artifact can only be built on the
   device. **Pinning a model needs no extra work** — drop the ONNX in and let scons compile it.
   Runtime switching is the expensive path: it must run `compile_modeld.py` on-device.
3. Qualify it on your own hardware before anyone drives on it.

See `integrate.html` on the site for the full walkthrough.

## Self-hosting

```bash
docker compose up -d                  # catalog + API on http://localhost:8000
```

A self-hosted instance clones upstream on first boot, refreshes every hour, retains records from
force-pushed PR heads, and builds a verified local blob mirror progressively. Set
`LOCAL_MIRROR_LIMIT` to control how many missing blobs each refresh downloads. Files not mirrored
yet return 503; files already gone from upstream are recorded explicitly. Once the sync is
complete, the named Docker volume contains everything needed to run air-gapped.
The bundled Caddy server keeps static pages and blobs out of Python and binds only to localhost;
put your normal HTTPS reverse proxy in front before exposing it publicly. Set
`OPENMODELS_API_BASE=https://models.example.com` so rendered API links use that public origin.

## Mirroring

Weights are mirrored to GitHub Releases because comma is free to GC LFS objects for unreachable
commits — for reverted and never-merged models this archive may be the only public copy.

```bash
python -m index.publish --repo owner/repo --limit 40 --dry-run
```

GitHub caps a release at **1000 assets**, so blobs are sharded across `blobs-NNNN` releases and
each file's shard is recorded in the index — a download URL is never reconstructed from the oid
alone. Blobs are fetched on demand and deleted after upload, so CI never holds the full archive;
`--limit` spreads the initial backfill across runs. Pending blobs return **503**; blobs that have
already disappeared from upstream return **410** rather than asking clients to retry forever.
The full upstream MIT notice is carried in every release’s notes.

## How it stays current

Detection is pure git. Each run fetches upstream branches and PR heads, reads their 133-byte LFS
pointers, and merges the result with the last published catalog. That last merge is essential:
a force-push erases an old PR ref upstream, but it must not erase the model, provenance, metadata,
or release placement from this archive. Identical file sets still deduplicate by content.

## Development

```bash
python -m pip install .
python index/test_metadata.py
python index/test_indexer.py
python index/test_compose.py
python api/test_api.py
python runtime/test_runtime.py
python -m index.indexer --repo /path/to/openpilot --limit 40   # index recent commits
python web/render.py                                           # render the static site
uvicorn api.main:app --reload
```

`AGENTS.md` documents the invariants that aren't obvious from the code — read it before changing
`index/`. The most important: **never `pickle.loads` the `output_slices` metadata**, which is
attacker-controlled.

## Attribution

Project code is MIT licensed. Models © comma.ai and are distributed under the
[openpilot MIT license](https://github.com/commaai/openpilot/blob/master/LICENSE); the complete
notice is preserved in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Every entry links to its
source commit and PR. **Not affiliated with or endorsed by comma.ai.** For subjective comparisons
of how models drive, see [sunnylink.wiki](https://sunnylink.wiki/models).

Removal requests: open an issue.
