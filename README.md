# OpenModels

An archive of [comma](https://github.com/commaai/openpilot) models, precompiled for comma 3X
hardware, plus a static directory site and a dependency-free Python SDK for verified downloads.

Daily CI discovers comma models in `commaai/openpilot` and mirrors the blobs into `blobs-NNNN`
Releases, checkpointing importer state so reverted and never-merged models stay reachable. The
same pipeline compiles the pinned stock supercombo off-device for Qualcomm a630 and publishes
it to a `builds-0001` Release. This is package version **0.1.0** and **schema 1**. Identity and
provenance are separate from execution support and consumer qualification.

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
python web/render.py --index data/index.json --out data/public --builds data/builds/builds.json
```

`index.html` is page one of the directory, `archive.html` lists historical models, and
`integrate.html` documents the downloads and SDK. `catalog.json` is the complete SDK snapshot;
`manifests/`, `recipes/` and `schemas/` contain the downloadable contracts; `builds.json` lists
recorded precompiled builds. Importer state lives on the `archive-state` branch (locally,
`data/index.json`) so it survives website rollback.

`compose.yaml` runs a self-hosted indexer plus Caddy over the static output. Model blobs are
served directly by Caddy or GitHub Releases, never through Python. See
[CI and deployment](docs/deployment.md) for bootstrap, precompilation, configuration and
rollback.

## Precompile

```bash
python -m ci.builds compile --catalog https://jjolano.github.io/openmodels/catalog.json \
  --store ./models --work ./builds --no-upload
```

Requires Linux x86-64 with `qemu-user-static`, LLVM, and Tinygrad at the revision in
`openmodels.profiles`. Only the pinned stock recipe and artifact compile, and only for the one
recorded a630 target. Builds are evidence of compilation: GPU parity, device execution and
timing remain unverified, and every record says so.

## Check changes

```bash
python -m pip install '.[test]' numpy zstandard
TINYGRAD="$(python -c 'from openmodels.profiles import TINYGRAD_REVISION as r; print(r)')"
python -m pip install "tinygrad @ git+https://github.com/tinygrad/tinygrad.git@$TINYGRAD"
python index/test_metadata.py
python index/test_indexer.py
python -m unittest discover -s tests -v
python -m ci.browser   # needs npm install --global agent-browser
```

Vendored compiler semantics can be checked against a pinned upstream checkout with
`DEV=CPU WARP_DEV=CPU python tests/check_reference.py --openpilot /path/to/openpilot`. Hardware
parity and timing require separate device evidence.

Archived comma models retain the upstream MIT license; see
[third-party notices](THIRD_PARTY_NOTICES.md).

Names come from comma's introducing commits, pinned community-wiki references and Sunnypilot's
catalog, matched to exact commits and complete artifact sets in `index/model_names.json`.
Attributed aliases remain searchable, unnamed models receive source-derived or generated labels
and stay in the directory. See [naming evidence and policy](docs/naming.md); a name never
establishes runtime support or equivalent fork tuning.
