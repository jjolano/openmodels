# OpenModels

An archive of [comma](https://github.com/commaai/openpilot) models, plus a static directory site
and a dependency-free Python SDK for verified downloads.

Daily CI discovers comma models in `commaai/openpilot` and mirrors the blobs into `blobs-NNNN`
Releases, checkpointing importer state so reverted and never-merged models stay reachable. This
is package version **0.1.0** and **schema 1**. Identity and provenance are separate from
execution support and consumer qualification.

## Browse and download

```bash
python -m pip install .
python -m openmodels --catalog https://jjolano.github.io/openmodels/catalog.json list --query supercombo
python -m openmodels --catalog catalog.json export RECIPE_ID > selected.json
python -m openmodels --catalog catalog.json fetch RECIPE_ID --store ./models
```

```python
from pathlib import Path
from openmodels import Catalog, ModelStore

catalog = Catalog.load("https://jjolano.github.io/openmodels/catalog.json")
package = ModelStore(Path("./models"), catalog).fetch(catalog.resolve("Stock supercombo (555f48c5)"))
package.verify()
```

Downloads verify the declared size and SHA-256 and rename into place atomically, so an
interrupted download never becomes installed. See
[the catalog contract](docs/catalog.md) for document identity, the CLI, cancellation and the
published guarantees. Consumers own selection, scheduling, qualification, activation and
output publication.

## Build and serve the site

```bash
python -m index.indexer --repo /path/to/openpilot --out data/index.json
python web/render.py --index data/index.json --out data/public
```

`index.html` is page one of the directory, `archive.html` lists historical models, and
`integrate.html` documents the downloads and SDK. `catalog.json` is the complete SDK snapshot;
`manifests/`, `recipes/` and `schemas/` contain the downloadable contracts. Importer state lives
on the `archive-state` branch (locally, `data/index.json`) so it survives website rollback.

`compose.yaml` runs a self-hosted indexer plus Caddy over the static output. Model blobs are
served directly by Caddy or GitHub Releases, never through Python. See
[CI and deployment](docs/deployment.md) for bootstrap, configuration and rollback.

## Check changes

```bash
python -m pip install '.[test]'
python index/test_metadata.py
python index/test_indexer.py
python -m unittest discover -s tests -v
python -m ci.browser   # needs npm install --global agent-browser, then agent-browser install --with-deps
```

Archived comma models retain the upstream MIT license; see
[third-party notices](THIRD_PARTY_NOTICES.md).

Names come from comma's introducing commits, pinned community-wiki references and Sunnypilot's
catalog, matched to exact commits and complete artifact sets in `index/model_names.json`.
Attributed aliases remain searchable, and unnamed models receive source-derived or generated labels. They appear in the current directory or historical archive according to upstream status. See [naming evidence and policy](docs/naming.md); a name never
establishes runtime support or equivalent fork tuning.
