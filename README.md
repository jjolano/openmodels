# OpenModels

A universal model directory, recipe composer, API and Python SDK, with an automatically
preserved archive of models from `commaai/openpilot`. Publishers can submit other model
families using the same contracts.

This is the first release: package version **0.1.0**, **schema 1**, and **`/v1`** HTTP endpoints.
Recipes preserve exact artifacts, source context and execution settings. Identity and
provenance are separate from execution support and consumer qualification.

## Use the SDK

The base SDK has no third-party dependencies. Install from this checkout:

```bash
python -m pip install .
python -m openmodels --catalog https://jjolano.github.io/openmodels/catalog.json list
python -m openmodels --catalog catalog.json export RECIPE_ID > selected.json
python -m openmodels --catalog catalog.json fetch RECIPE_ID --store ./models
```

See [contracts and integration](docs/universal.md) for Python composition, offline snapshots,
verified downloads and the optional runner contract. Consumers own scheduling, qualification,
activation and output publication. The separate `openmodels-runner-tinygrad` package implements
a pinned stock supercombo profile for QCOM/comma 3X; device compilation, output parity and
timing qualification still require hardware validation.

## Publish and serve

```bash
python -m pip install '.[server]'
python -m index.indexer --repo /path/to/openpilot --out data/index.json
python web/render.py --index data/index.json --out data/public
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`index.html` includes every model, `archive.html` filters historical models, and
`compose.html` is the advanced composer. `catalog.json` is the complete SDK snapshot;
`manifests/`, `recipes/` and `schemas/` contain downloadable contracts. Importer state lives
on the `archive-state` branch (locally, `data/index.json`) so it survives website rollback.
[Publisher submissions](publishers/README.md) enter through reviewed JSON files.

The API exposes `/v1/catalog`, `/v1/models`, `/v1/models/{identity}`, `/v1/profiles`, `/v1/manifests/{digest}`,
`/v1/artifacts/{digest}`, `/v1/schemas/{name}`, `/v1/status` and `POST /v1/compose`.
Interactive documentation is at `/docs`. Browser composition calls the same SDK validation
through this API; static-only mirrors support recipe downloads and SDK composition.
Set `OPENMODELS_API_BASE` when rendering against a separately hosted API.

`compose.yaml` runs the API, static server and scheduled local importer. `OPENMODELS_DATA`
selects the data directory. Model blobs are served directly by Caddy or GitHub Releases.
Actions checkpoints discoveries before mirroring, retains immutable catalog snapshots in
Releases, and deploys Pages from a tested artifact. Archives are append-only; release locations
come from recorded mirror metadata. See [CI and deployment](docs/deployment.md) for bootstrap,
API synchronization, configuration and rollback.

## Check changes

```bash
python -m pip install '.[server,test]' numpy
python -m pip install --no-deps ./runners/tinygrad
python index/test_metadata.py
python index/test_indexer.py
python -m unittest discover -s tests -v
```

The [integration guide](docs/universal.md) also describes pinned reference checks and the
remaining device validation. Archived comma models retain the upstream MIT license; see
[third-party notices](THIRD_PARTY_NOTICES.md).

## Build a model switcher

Browse named models and their exact source configurations on the [directory](https://jjolano.github.io/openmodels/). Use `Catalog.models()` and `ModelSwitcher` to apply your fork’s support policy, download with progress and cancellation, and prepare through a runner. See [the integration guide](docs/switcher.md) and run `python examples/switcher.py --demo`. Static `models.json` offers the same discovery view to other languages; the complete catalog provides immutable manifests and artifact locations.

Names come from comma’s introducing commits, pinned community-wiki references and Sunnypilot’s catalog, matched to exact commits and complete artifact sets in `index/model_names.json`. Attributed aliases remain searchable. Unnamed models receive source-derived or generated labels and remain in the main directory. See [naming evidence and policy](docs/naming.md); a name never establishes runtime support or equivalent fork tuning.
