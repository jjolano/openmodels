# Build a model switcher

The website and SDK use the same model directory. A **model** is a named choice;
its **variants** are exact recipes with a profile, artifacts and source configuration.
Consumers decide which recipes their hardware, runner and integration support.
The catalog itself does not qualify models for driving.

## List, select and install

Install the dependency-free SDK with `python -m pip install .` from this repository.
No API service is required: GitHub Pages supplies the complete static catalog.

```python
from threading import Event
from openmodels import Catalog, ModelSwitcher

catalog = Catalog.load("https://jjolano.github.io/openmodels/catalog.json")
# Filled by the consumer's reviewed integration, never inferred from a model's name.
approved_recipes = {"<exact recipe SHA-256 reviewed by this consumer>"}

def support(recipe):
    return None if recipe.id in approved_recipes else "Not supported by this consumer"

switcher = ModelSwitcher(catalog, "./models", support=support)
choices = switcher.list_models()
# Display model['name']; let the user select one of model['variants'].
# Each variant includes recipe, label, supported, reason and installed.
selected_recipe = choices[0]["variants"][0]["recipe"]  # Only after a user selection.
cancel = Event()
package = switcher.install(
    selected_recipe,
    on_progress=lambda artifact, received, total: print(artifact, received, total),
    cancelled=cancel.is_set,
)
package.verify()
```

Handle an empty choices list by explaining that no models match the consumer policy.
Use `list_models(include_unsupported=True)` to display rejected choices and their reasons.
Unavailable downloads are hidden by default unless a verified package is already installed.
`Catalog.models()` is useful for browsing without applying a consumer policy; browsing a
model does not mean the local host supports it. Selection and installation use full recipe
identifiers, so renames and catalog ordering cannot silently change the selected weights.

Operations are synchronous. A GUI can call them in its existing worker and forward
progress through its normal UI mechanism. Set the cancellation event from the UI;
`DownloadCancelled` is distinct from a download failure. Cancellation is checked between
network chunks and before installation; a blocking network read can take up to its 30-second
timeout. Once the atomic installation has completed, it remains installed.

`switcher.status` returns `state`, `recipe` and `error`. States are `idle`, `downloading`,
`installed`, `preparing`, `prepared`, `cancelled`, and `failed`. The recipe describes the
latest attempted operation; `switcher.package` and `switcher.build` retain the last successful
results if a later operation fails. Do not run overlapping operations on one switcher.
Installed status is verified from the store; operation status is in-memory and starts idle
after restart. A failed or cancelled download never exposes a partial installed package.
Complete verified artifact cache entries may remain reusable.

## Prepare with an explicit runner

```python
# runner and target are constructed by the consumer for a supported integration.
build = switcher.prepare(selected_recipe, runner, target)
```

Preparation checks policy again and verifies the installed package before invoking the
runner. Downloading does not automatically import or choose a runner. The consumer owns
qualification, admission, activation, scheduling and rollback. The optional Tinygrad runner
supports only its pinned stock profile and target; see [the runner contract](universal.md#optional-qcom-runner).

## Runnable example

```bash
python examples/switcher.py --demo
```

The demo starts a temporary loopback server, lists a named demonstration model, downloads
harmless fixture bytes and verifies the installed package. It uses no driving weights or
inference runtime.

For a real catalog, create `policy.json` containing `{"allowed_recipe_ids": []}` and fill
that list with the recipes your consumer has reviewed. An empty list supports nothing.

```bash
python examples/switcher.py --catalog https://jjolano.github.io/openmodels/catalog.json --policy policy.json list
python examples/switcher.py --catalog https://jjolano.github.io/openmodels/catalog.json --policy policy.json --store ./models install RECIPE_SHA256
```

The example hands back a verified local package. Connect that result to your consumer's
existing preparation and activation flow; OpenModels does not change an active model.
