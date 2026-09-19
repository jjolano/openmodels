# CI and deployment

## Pipelines

- **Verify** runs on PRs/main and is reused by publication. It checks Python 3.11/3.12, archive
  and contract tests, deterministic site output, and browser browsing under `/openmodels/`.
- **Refresh archive** runs daily or manually. Its read-only job restores the archive checkpoint
  and scans upstream. A separate writer validates and checkpoints discoveries **before**
  mirroring, then saves mirror locations. Interrupted uploads are rediscovered from Release
  listings on retry; the earlier checkpoint already preserves provenance.
- **Publish Pages** runs after refresh, relevant main changes, or a manual request. It restores
  a pinned archive commit, builds the site, validates exports and links, retains the snapshot,
  and deploys that exact artifact with the official Pages actions. UI changes never rescan
  upstream.

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
3. Confirm `archive-state` exists and the Pages deployment succeeds. Keep the old `gh-pages`
   branch until the checkpoint has been checked. It is no longer updated or used for hosting;
   deleting it afterward is optional.
   `METADATA_LIMIT` and `UPLOAD_LIMIT` default to 20 and 40 per refresh. Set repository
   variable `OPENMODELS_CATALOG` to use a different catalog origin.

The automatic main publication can fail before bootstrap because `archive-state` does not yet
exist. The explicit first refresh creates it and publishes the site. Do not bypass this failure
with an empty catalog: historical PR-only models may have no remaining upstream refs.

## Repeatability and rollback

`deployment.json` records the source code commit, the archive commit and the catalog digest.
Each full snapshot is retained once as `catalog-DIGEST/DIGEST.json` in Releases, without
clobbering existing assets. Each deployment record is also retained there as
`deployment-SHA256.json`, including subsequent UI builds of the same catalog. Workflow
artifacts are only temporary delivery copies.

To roll back, run **Publish Pages** on main with `code_revision` and `archive_revision` from the
desired deployment record. The code commit must be an ancestor of main and must include this
publication tooling. Empty inputs select the current code and latest archive; they are not an
exact rollback. This operation never rewinds or deletes `archive-state`.

`ci.site build` is byte-deterministic for the same inputs.

## Self-hosting

`compose.yaml` runs an indexer container that clones `commaai/openpilot`, discovers models on
a loop, mirrors the blobs it finds into a local `blobs/` directory, and renders the same static
site; Caddy serves `/data/public`. `BLOB_BACKEND=local`, `LOCAL_MIRROR_LIMIT` and `INTERVAL`
configure it. No API service exists — the catalog is a static file.

Check the Actions summaries for archive freshness and retry counts, and Pages `deployment.json`
for deployed identity. Qualification and device timing remain separate work; CI runner tests do
not establish those properties.
