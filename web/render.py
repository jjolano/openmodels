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

DISCLAIMER = (
  "This registry asserts <strong>blob identity and upstream provenance</strong>. It does not "
  "assert that a model is safe to drive, or that two models are interchangeable."
)
API_BASE = os.environ.get("OPENMODELS_API_BASE", "").rstrip("/")
BLOB_BACKEND = os.environ.get("BLOB_BACKEND", "github")

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#fbfbfa; --panel:#fff; --ink:#16150f; --muted:#6b6a63; --line:#e3e2dc;
  --accent:#3d5a3d; --code:#f4f4f1;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#131311; --panel:#1b1b18; --ink:#eceae2; --muted:#9b998f; --line:#2e2e29;
  --accent:#9dbf9d; --code:#232320;
}}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 72px}
a{color:var(--accent)}
header.top{border-bottom:1px solid var(--line);margin-bottom:20px;padding:18px 0 12px}
header.top h1{margin:0;font-size:23px;letter-spacing:-.02em}
header.top h1 a{color:inherit;text-decoration:none}
header.top p{margin:6px 0 0;color:var(--muted);max-width:62ch}
nav.top{margin-top:14px;display:flex;column-gap:16px;row-gap:0;flex-wrap:wrap;font-size:14px}
[hidden]{display:none!important}
.meta{color:var(--muted);font-size:13px;display:flex;gap:9px;flex-wrap:wrap;align-items:center}
code{background:var(--code);padding:1.5px 5px;border-radius:5px;font-size:13px}
button{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;
  padding:8px 14px;font:inherit;font-size:14px;cursor:pointer}
footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:13px}
.directory-list{list-style:none;padding:0}
.directory-list>li{border-bottom:1px solid var(--line);padding:14px 0;overflow-wrap:anywhere}
.directory-list summary{cursor:pointer}
.directory-list code{overflow-wrap:anywhere}
.universal-composer{margin:24px 0;padding:16px 0;border-block:1px solid var(--line)}
.universal-composer>summary{font-weight:600;cursor:pointer}
.recipe-fields{display:grid;gap:8px;margin-top:16px;max-width:100%}
.recipe-fields select,.recipe-fields input,.recipe-fields textarea{width:100%;min-width:0;padding:10px;
  font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:8px}
.recipe-fields fieldset{min-width:0;border:0;padding:0}
.recipe-fields button{justify-self:start}
.recipe-fields button:disabled{cursor:not-allowed;opacity:.6}
.recipe-fields :focus-visible,.directory-list summary:focus-visible,.universal-composer summary:focus-visible{
  outline:2px solid var(--accent);outline-offset:3px}
h1.title{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}
"""

def shell(title: str, body: str) -> str:
  if API_BASE:
    api_link = f'<a href="{html.escape(API_BASE, quote=True)}/docs">API</a>'
  elif BLOB_BACKEND == "local":
    api_link = f'<a href="docs">API</a>'
  else:
    api_link = ""
  return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head><body><div class="wrap">
<header class="top">
  <h1><a href="index.html">openmodels</a></h1>
  <p>Openpilot models for people and the forks they drive.</p>
  <nav class="top">
    <a href="index.html">Models</a>
    <a href="integrate.html">Build a model switcher</a>
    <a href="archive.html">Archive</a>
    <a href="compose.html">Composer</a>
    {api_link}
  </nav>
</header>
{body}
<footer>
  <p>{DISCLAIMER}</p>
  <p>Comma models &copy; comma.ai, distributed under the
     <a href="https://github.com/commaai/openpilot/blob/master/LICENSE">openpilot MIT license</a>
     and sourced from <a href="https://github.com/commaai/openpilot">commaai/openpilot</a>.
     Other publishers retain the licenses recorded in their manifests. Not affiliated with or endorsed by comma.ai.
     For subjective comparisons of how models drive, see
     <a href="https://sunnylink.wiki/models">sunnylink.wiki</a>.</p>
</footer>
</div></body></html>
"""


def render(index_path: Path, out_dir: Path) -> int:
  from index.registry import publish, atomic_write
  from web.directory import render as render_directory

  index = json.loads(index_path.read_text())
  catalog = publish(index, out_dir, publishers=Path(__file__).resolve().parents[1] / "publishers",
                    blob_base="blobs" if BLOB_BACKEND == "local" else None)
  for entry in catalog.data["entries"]:
    recipe = catalog.resolve(entry["recipe"])
    atomic_write(out_dir / "recipes" / f"{recipe.id}.json", catalog.export(recipe))
  atomic_write(out_dir / "compose.html", render_directory(catalog, shell, api_base=API_BASE,
                                                        api_enabled=bool(API_BASE) or BLOB_BACKEND == "local"))
  from web.models import listing, detail, guide, filename, discovery, page_filename, PAGE_SIZE
  from openmodels.contracts import dumps, sha256
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
  return 2 + listing_pages + len(models)


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
