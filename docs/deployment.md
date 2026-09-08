# CI and deployment

## Pipelines

- **Verify** runs on PRs/main and is reused by publication and SDK releases. It checks Python
  3.11/3.12, archive and contract tests, real publisher submissions, deterministic site output,
  both wheels outside the checkout, and browser composition-request exports under `/openmodels/`.
- **Refresh archive** runs daily or manually. Its read-only job restores the archive checkpoint
  and scans upstream. A separate writer validates and checkpoints discoveries **before** mirroring,
  then saves mirror locations and invokes Pages publication. Interrupted uploads are rediscovered
  from Release listings on retry; the earlier checkpoint already preserves provenance.
- **Publish Pages** runs after refresh, relevant main changes, or a manual request. It restores a
  pinned archive commit, builds the site, validates exports/links, retains the snapshot, and deploys
  that exact artifact with the official Pages actions. UI changes never rescan upstream.
- **Release SDK** runs for `v*` tags. It requires the tagged commit to be on main, verifies both
  package versions match the tag, and publishes the tested wheels plus SHA256SUMS to a GitHub
  Release. It does not rebuild between verification and upload. PyPI publishing is not configured.
- **Experimental QCOM build** runs manually on an ordinary Ubuntu x86-64 runner. It builds
  the pinned stock model with the [off-device compiler](universal.md#experimental-off-device-compilation)
  and uploads the pickle plus its evidence report as a 14-day Actions artifact. It has read-only
  repository permissions and does not publish builds into the catalog or create runner receipts.

CodeQL and Dependabot remain separate. Only archive/Release writers receive `contents: write`;
Pages deployment receives `pages: write` and `id-token: write`. PR jobs have no publishing credentials.
Refreshes and Pages publications each serialize their writers; conflicting archive pushes fail.

## First deployment

1. Merge the implementation onto main. Configure repository **Settings → Pages → Source →
   GitHub Actions**, and restrict the `github-pages` environment to main.
2. Run **Refresh archive** manually with `bootstrap=true`. This reads the existing
   `gh-pages:index.json`, validates it, and saves the resulting archive to `archive-state`.
   Missing state or a transport error fails loudly; it never starts from an empty archive.
3. Confirm `archive-state` exists and the refresh and Pages deployment succeed. Keep the old
   `gh-pages` branch until the checkpoint has been checked. It is no longer updated or used for
   hosting; deleting it afterward is optional and is not part of the automated workflow.
4. Pages supports the workbench, browsing, downloads and SDK handoff without an API.
   Optionally set `OPENMODELS_API_BASE` to an HTTPS API origin (without `/v1`) to add an
   API-reference link. `METADATA_LIMIT` and `UPLOAD_LIMIT` default to 20 and 40 per refresh.

The automatic main publication can fail before bootstrap because `archive-state` does not yet
exist. The explicit first refresh creates it and publishes the site. Do not bypass this failure
with an empty catalog: historical PR-only models may have no remaining upstream refs.

## API synchronization

A separately hosted API is optional; the static workbench exports requests for the Python SDK.
For direct API consumers, run the existing API container on your chosen host. If another browser
application calls it, set `OPENMODELS_CORS_ORIGINS` to that application's exact origin, such as
`https://jjolano.github.io` (the `/openmodels/` path is not part of an origin). Authentication
cookies are not required.

The API reads `OPENMODELS_DATA/public/catalog.json`; it does not need to run the comma importer.
On the host, synchronize the published catalog with:

```bash
python -m ci.site sync --url https://jjolano.github.io/openmodels/catalog.json --data /data
```

For a pinned publication, substitute its durable Release URL and add `--sha256 DIGEST`:

```text
https://github.com/OWNER/REPO/releases/download/catalog-DIGEST/DIGEST.json
```

Schedule the sync command on the API host, for example every five minutes, or run it after a
Pages deployment. The helper validates the full graph before atomically replacing the catalog;
a failed fetch leaves the last valid snapshot in place. Configure the origin yourself; the
helper never downloads from arbitrary browser-supplied URLs. The Docker image includes `ci/`
for this operation. A separate API host and its scheduler are not provisioned by these workflows.

Direct API consumers can send their catalog digest as a quoted `If-Match` header with
composition requests. An API on another revision responds HTTP 412; synchronize the host or
load its current snapshot before retrying. GET `/v1/status` reports the active digest for
comparison with Pages `deployment.json`. The workbench instead exports Python and CLI examples
pinned to its displayed snapshot, so API synchronization lag does not affect that workflow.

## Repeatability and rollback

`deployment.json` records the source code commit, archive commit, catalog digest and API origin.
Each full snapshot is retained once as `catalog-DIGEST/DIGEST.json` in Releases, without
clobbering existing assets. Each deployment record is also retained there as
`deployment-SHA256.json`, including subsequent UI builds of the same catalog. The snapshot
embeds exact manifest strings, so older catalogs remain usable
when their former Pages paths disappear. Workflow artifacts are only temporary delivery copies.

To roll back, run **Publish Pages** on main with `code_revision`, `archive_revision`, and
`api_base` from the desired deployment record. The code commit must be an ancestor of main and
must include this publication tooling. Empty inputs select the current code/latest archive and
configured API variable; they are not an exact rollback. Synchronize the API to the same retained
snapshot. This operation never rewinds or deletes `archive-state`.

Check the Actions summaries for archive freshness and retry counts, Pages `deployment.json`
for deployed identity, and `/v1/status` for API identity. Artifact availability is visible in the
site and catalog. Compilation, GPU output parity and device timing remain separate qualification
work; CI runner tests do not establish those properties.
