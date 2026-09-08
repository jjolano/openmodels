#!/usr/bin/env python3
"""Publish the model directory, manifests and importer archive state."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openmodels.contracts import dumps, sha256

DISCLAIMER = (
  "This registry asserts <strong>blob identity and upstream provenance</strong>. It does not "
  "assert that a model is safe to drive, or that two models are interchangeable."
)
API_BASE = os.environ.get("OPENMODELS_API_BASE", "").rstrip("/")
BLOB_BACKEND = os.environ.get("BLOB_BACKEND", "github")

CSS = Path(__file__).with_name("style.css").read_text()
STYLE_FILE = "style-" + sha256(CSS.encode())[:16] + ".css"


def shell(title: str, body: str) -> str:
  api_link = f'<a href="{html.escape(API_BASE, quote=True)}/docs">API reference</a>' if API_BASE else ''
  return f"""<!doctype html>
<html lang="en" data-theme="dark"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>{html.escape(title)}</title>
<script>try{{document.documentElement.dataset.theme=localStorage.getItem('openmodels-theme')==='light'?'light':'dark'}}catch(_){{}}</script>
<link rel="stylesheet" href="{STYLE_FILE}">
</head><body><a class="skip-link" href="#content">Skip to content</a><div class="wrap">
<header class="top">
<a class="brand" href="index.html" aria-label="OpenModels workbench"><svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><path d="M8 8h16v16H8zM8 16h16M16 8v16" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 8h8v8H4zM20 20h8v8h-8z" fill="currentColor"/></svg>openmodels</a>
<nav class="top" aria-label="Main navigation"><a href="index.html">Workbench</a><a href="models.html">Models</a><a href="integrate.html">Developers</a></nav>
<button id="theme-toggle" type="button" aria-label="Switch to light theme">Light theme</button>
</header>
<div id="content" tabindex="-1">{body}</div>
<footer><span>Open models. Explicit configurations.</span><div><a href="catalog.json">Catalog JSON</a>{api_link}<a href="https://github.com/jjolano/openmodels">GitHub</a></div>
<details><summary>Provenance and licenses</summary><p>{DISCLAIMER}</p><p>Comma models © comma.ai under the <a href="https://github.com/commaai/openpilot/blob/master/LICENSE">MIT license</a>. Other publishers retain their recorded licenses. Not affiliated with comma.ai. <a href="https://sunnylink.wiki/models">Community model descriptions</a>.</p></details></footer>
</div><script>
const themeButton=document.getElementById('theme-toggle');
function themeLabel(){{const light=document.documentElement.dataset.theme==='light';themeButton.textContent=light?'Dark theme':'Light theme';themeButton.setAttribute('aria-label','Switch to '+(light?'dark':'light')+' theme')}}
themeLabel();themeButton.addEventListener('click',()=>{{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='light'?'dark':'light';try{{localStorage.setItem('openmodels-theme',document.documentElement.dataset.theme)}}catch(_){{}}themeLabel()}});
const path=location.pathname.split('/').pop();const active=(!path||path==='index.html'||path==='compose.html')?'index.html':path==='integrate.html'?'integrate.html':'models.html';document.querySelectorAll('nav.top a').forEach(a=>{{if(a.getAttribute('href')===active)a.setAttribute('aria-current','page')}});
</script></body></html>"""


def render(index_path: Path, out_dir: Path) -> int:
  from index.registry import publish, atomic_write
  from web.directory import component_index, render as render_directory

  index = json.loads(index_path.read_text())
  catalog = publish(index, out_dir, publishers=Path(__file__).resolve().parents[1] / "publishers",
                    blob_base="blobs" if BLOB_BACKEND == "local" else None)
  for entry in catalog.data["entries"]:
    recipe = catalog.resolve(entry["recipe"])
    atomic_write(out_dir / "recipes" / f"{recipe.id}.json", catalog.export(recipe))
  component_raw = dumps(component_index(catalog))
  component_url = "components-" + sha256(component_raw.encode())[:16] + ".json"
  atomic_write(out_dir / component_url, component_raw)
  script = Path(__file__).with_name("workbench.js").read_text()
  script_url = "workbench-" + sha256(script.encode())[:16] + ".js"
  atomic_write(out_dir / script_url, script)
  atomic_write(out_dir / STYLE_FILE, CSS)
  workspace = render_directory(catalog, shell, component_url=component_url, script_url=script_url)
  atomic_write(out_dir / "index.html", workspace)
  atomic_write(out_dir / "compose.html", workspace)
  from web.models import listing, detail, guide, filename, discovery, page_filename, PAGE_SIZE
  records = discovery(catalog)
  raw = dumps(records)
  discovery_url = "discovery-" + sha256(raw.encode()) + ".json"
  atomic_write(out_dir / discovery_url, raw)
  listing_pages = 0
  for archive in (False, True):
    count = sum(not archive or m["archived"] for m in records)
    for page in range(1, max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE) + 1):
      atomic_write(out_dir / page_filename(page, archive), listing(catalog, shell, archive=archive, page=page,
                                                                 records=records, discovery_url=discovery_url))
      listing_pages += 1
  atomic_write(out_dir / "integrate.html", guide(shell))
  models = catalog.models(include_archive=True)
  for model in models:
    atomic_write(out_dir / filename(model), detail(catalog, model, shell))
  atomic_write(out_dir / ".nojekyll", "")
  return 3 + listing_pages + len(models)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--index", default="data/index.json")
  parser.add_argument("--out", default="data/public")
  args = parser.parse_args()
  count = render(Path(args.index), Path(args.out))
  print(f"rendered {count} pages -> {args.out}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
