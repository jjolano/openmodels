# openmodels

A public archive of openpilot driving models, indexed automatically from `commaai/openpilot`
git history and served over an HTTP API any fork can pull from.

Most interesting openpilot models are **reverted from master shortly after landing** — they
survive only at specific commit SHAs, and every fork that wants to offer model choice has to
rediscover them by hand. This indexes all of them, with the provenance needed to use one.

**175 bundles · 230 distinct files · ~7.9 GB · 2020-11 → present**

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
GET  /v1/files/{oid}/download          302 → release asset
GET  /v1/status                        freshness, counts, upstream head
GET  /docs                             OpenAPI explorer
```

Pull a model, verifying as you go:

```bash
python clients/reference.py --list
python clients/reference.py --pull <bundle_id> --out ./models
```

The reference client verifies every blob against its oid, **default-denies reverted and PR-only
models**, and falls back to the static mirror when the API is unreachable.

## Integration library (`runtime/`)

Drop-in, dependency-free (stdlib only), and deliberately **outside the control path** — it never
runs inference or touches actuation. Two pieces:

**`runtime/manager.py`** — everything behind a model-picker UI, with no opinion about the UI:

```python
from runtime.manager import Catalog, ModelStore, download_bundle

cat   = Catalog.load(API, mirror=MIRROR, cache=Path("cache.json"))
years = cat.group_by_year(kind="driving")        # folder-style picker
store = ModelStore(Path("models"))

download_bundle(cat, bundle_id, store, on_progress=lambda p: bar(p.fraction, p.eta_seconds))
store.set_active(bundle_id)
store.active_constants()      # -> the constants to apply with this model
```

Three behaviours are not configurable, because getting them wrong ships a model that appears to
work: every file is verified against its oid, withdrawn models are excluded unless asked for,
and files are staged then moved atomically so an interrupted download can never look installed.

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

Compilation itself still requires the device — the QCOM backend opens `/dev/kgsl-3d0` — so the
plan is produced anywhere and executed on-target.

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

See `/integrate` on the site for the full walkthrough.

## Self-hosting

```bash
docker compose up -d                  # index + serve; no credentials required
docker compose --profile web up -d    # ...also terminate TLS with caddy
```

A self-hosted instance is a **full peer**: it indexes from upstream directly and depends on
nothing we operate. Detection is pure git (anonymous `ls-remote` + blobless fetch) and LFS blobs
need no auth, so `GITHUB_TOKEN` is only needed to *publish*.

`BLOB_BACKEND=github` (default) redirects downloads to GitHub Releases so the ~8 GB of weights
and their bandwidth stay off your host. `BLOB_BACKEND=local` serves from your own volume and
works air-gapped after the first sync.

## Mirroring

Weights are mirrored to GitHub Releases because comma is free to GC LFS objects for unreachable
commits — for reverted and never-merged models this archive may be the only public copy.

```bash
python -m index.publish --repo OWNER/REPO --limit 40 --dry-run
```

GitHub caps a release at **1000 assets**, so blobs are sharded across `blobs-NNNN` releases and
each file's shard is recorded in the index — a download URL is never reconstructed from the oid
alone. Blobs are fetched on demand and deleted after upload, so CI never holds the full archive;
`--limit` spreads the initial backfill across runs. Until a blob is mirrored, its download
endpoint returns **503**, never a redirect to a 404.

## How it stays current

Pure git, no GitHub API, no token. `git ls-remote 'refs/pull/*/head'` returns every PR head
anonymously in one call; the ref→sha map is diffed against the last run and only changed refs are
fetched. Models are 133-byte LFS pointers in the tree, so the entire history is enumerable
without downloading a single blob — and identical file sets across commits dedup for free.

## Development

```bash
python index/test_metadata.py                                  # parser + security self-check
python -m index.indexer --repo /path/to/openpilot --limit 40   # index recent commits
python web/render.py                                           # render the static site
uvicorn api.main:app --reload
```

`AGENTS.md` documents the invariants that aren't obvious from the code — read it before changing
`index/`. The most important: **never `pickle.loads` the `output_slices` metadata**, which is
attacker-controlled.

## Attribution

Models © comma.ai, MIT licensed, sourced from
[commaai/openpilot](https://github.com/commaai/openpilot). Every entry links to its source commit
and PR. **Not affiliated with or endorsed by comma.ai.** For subjective comparisons of how models
drive, see [sunnylink.wiki](https://sunnylink.wiki/models) — that's editorial work this archive
deliberately doesn't attempt.

Removal requests: open an issue.
