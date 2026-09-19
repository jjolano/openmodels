# CI and deployment

## Pipelines

- **Verify** runs on PRs/main and is reused by publication. It checks Python 3.11/3.12, archive
  and contract tests, deterministic site output, the renderer's build blocks, and browser
  browsing under `/openmodels/`.
- **Refresh archive** runs daily or manually. Its read-only job restores the archive checkpoint
  and scans upstream. A separate writer validates and checkpoints discoveries **before**
  mirroring, then saves mirror locations. Interrupted uploads are rediscovered from Release
  listings on retry; the earlier checkpoint already preserves provenance.
- **Precompile** runs after that writer, on `ubuntu-24.04` with `qemu-user-static` and LLVM.
  It compiles the pinned stock recipe for a630 and publishes the artifact, its record and the
  refreshed `builds.json` into the `builds-0001` Release. The Pages job depends on it only
  tolerantly: a failed compile leaves the archive deployment green and publishes the site with
  the record set already in the Release.
- **Publish Pages** runs after refresh, relevant main changes, or a manual request. It restores
  a pinned archive commit, downloads the current builds manifest, builds the site, validates
  exports, links and build records, retains the snapshot, and deploys that exact artifact with
  the official Pages actions. UI changes never rescan upstream.

CodeQL and Dependabot remain separate. Only archive/Release writers receive `contents: write`;
Pages deployment receives `pages: write` and `id-token: write`. PR jobs have no publishing
credentials. Refreshes and Pages publications each serialize their writers; conflicting archive
pushes fail.

## First deployment

1. Merge the implementation onto main. Configure repository **Settings → Pages → Source →
   GitHub Actions**, and restrict the `github-pages` environment to main.
2. Run **Refresh archive** manually with `bootstrap=true`. This reads the existing
   `gh-pages:index.json`, validates it, and saves the resulting archive to `archive-state`.
   Missing state or a transport error fails loudly; it never starts from an empty archive.
3. Confirm `archive-state` exists and the refresh, precompile and Pages deployment succeed.
   Keep the old `gh-pages` branch until the checkpoint has been checked. It is no longer
   updated or used for hosting; deleting it afterward is optional.
4. The first precompile publishes `builds.json` to `builds-0001`. Until it succeeds the site
   deploys with an empty manifest and no model page shows a build block, which is the correct
   rendering of "no build is recorded".
   `METADATA_LIMIT` and `UPLOAD_LIMIT` default to 20 and 40 per refresh. Set repository
   variable `OPENMODELS_CATALOG` to compile against a different catalog origin.

The automatic main publication can fail before bootstrap because `archive-state` does not yet
exist. The explicit first refresh creates it and publishes the site. Do not bypass this failure
with an empty catalog: historical PR-only models may have no remaining upstream refs.

## Precompiled builds

`python -m ci.builds compile` is the documented local command and the same entry point CI
runs. It needs Linux x86-64, `qemu-user-static`, LLVM, and Tinygrad installed from the Git
revision in `openmodels.profiles`; the toolchain archive is pinned by URL and SHA-256 in
`ci/qcom/toolchain.py`. If any of those move, update the constants and the `precompile` job
together.

```bash
python -m ci.builds compile --repo OWNER/REPO --catalog https://jjolano.github.io/openmodels/catalog.json \
  --store /var/tmp/models --work /var/tmp/qcom --no-upload
```

`--no-upload` writes `model.pkl`, `target.json`, `report.json`, `build-<id>.json` and
`builds.json` under `--work` and uploads nothing, which is what a local site build needs. The
`builds-0001` release is created on first upload with `THIRD_PARTY_NOTICES.md` as its notes.
Repeating a compile with unchanged inputs logs `unchanged build <id>` and uploads nothing: the
build identity covers the recipe document, the source artifact, the implementation digest and
the toolchain digest. A publishing run consults the Release before compiling, so a refresh on a
runner with no work directory neither recompiles nor re-records a published build; `--no-upload`
never contacts the Release and stays a purely local build. Never overwrite a release asset with
different bytes; a differing record is a hard error.

Records keep `gpu_validated: false` and `device_validated: false`. Compilation on x86-64 is
not device evidence, and no pipeline here claims it.

## Repeatability and rollback

`deployment.json` records the source code commit, the archive commit, the catalog digest and
the builds manifest digest (`builds_revision`, empty when the build was run with no `--builds`
input). Each full snapshot is retained once as `catalog-DIGEST/DIGEST.json` in Releases,
without clobbering existing assets. Each deployment record is also retained there as
`deployment-SHA256.json`, including subsequent UI builds of the same catalog. Workflow
artifacts are only temporary delivery copies.

To roll back, run **Publish Pages** on main with `code_revision`, `archive_revision` and
`builds_revision` from the desired deployment record. The code commit must be an ancestor of
main and must include this publication tooling. Empty inputs select the current code, the
latest archive and the current builds manifest; they are not an exact rollback. This operation
never rewinds or deletes `archive-state`.

`ci.site build` is byte-deterministic for the same inputs, because `builds.json` derives its
`generated_at` from the newest record rather than the wall clock.

## Self-hosting

`compose.yaml` runs an indexer container that clones `commaai/openpilot`, discovers models on
a loop, mirrors the blobs it finds into a local `blobs/` directory, and renders the same static
site; Caddy serves `/data/public`. `BLOB_BACKEND=local`, `LOCAL_MIRROR_LIMIT` and `INTERVAL`
configure it. No API service exists — the catalog is a static file.

Check the Actions summaries for archive freshness and retry counts, Pages `deployment.json`
for deployed identity, and `builds.json` for artifact identity. Compilation, GPU output parity
and device timing remain separate qualification work; CI runner tests do not establish those
properties.
