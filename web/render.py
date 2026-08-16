#!/usr/bin/env python3
"""Render the static site from index.json.

Pre-rendered rather than client-fetched so the whole catalog is readable with JavaScript
disabled, is indexable, and keeps working from the CDN mirror when the API is down. The only
JavaScript is filtering, layered over DOM that is already there.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
  --accent:#3d5a3d; --warn:#8a5a1f; --danger:#8f3232; --code:#f4f4f1;
  --radius:10px; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --code-font:"JetBrains Mono","IBM Plex Mono","Roboto Mono","DejaVu Sans Mono",
              ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#131311; --panel:#1b1b18; --ink:#eceae2; --muted:#9b998f; --line:#2e2e29;
  --accent:#9dbf9d; --warn:#d9a760; --danger:#e08585; --code:#232320;
}}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 72px}
a{color:var(--accent)}
header.top{border-bottom:1px solid var(--line);margin-bottom:28px;padding:26px 0 20px}
header.top h1{margin:0;font-size:23px;letter-spacing:-.02em}
header.top h1 a{color:inherit;text-decoration:none}
header.top p{margin:6px 0 0;color:var(--muted);max-width:62ch}
nav.top{margin-top:14px;display:flex;gap:16px;flex-wrap:wrap;font-size:14px}
.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);
  border-radius:var(--radius);padding:12px 15px;margin:0 0 24px;font-size:14px;color:var(--muted)}
.note strong{color:var(--ink)}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.controls input,.controls select{background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-radius:8px;padding:8px 11px;font:inherit;font-size:14px}
.controls input{flex:1;min-width:220px}
.pick{display:flex;flex-direction:column;gap:6px;flex:1;min-width:240px}
.pick input{font-size:13px;padding:6px 10px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:15px 17px;display:flex;flex-direction:column;gap:9px}
.card h3{margin:0;font-size:15.5px;line-height:1.35;letter-spacing:-.01em}
.card h3 a{text-decoration:none;color:inherit}
.card h3 a:hover{color:var(--accent)}
.meta{color:var(--muted);font-size:13px;display:flex;gap:9px;flex-wrap:wrap;align-items:center}
.badges{display:flex;gap:6px;flex-wrap:wrap}
.badge{font-size:11.5px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);
  color:var(--muted);white-space:nowrap}
.badge.merged{color:var(--accent);border-color:currentColor}
.badge.reverted{color:var(--danger);border-color:currentColor}
.badge.pr_only{color:var(--warn);border-color:currentColor}
.badge.head{color:var(--accent);border-color:currentColor}
.feat{font-family:var(--mono);font-size:12px;color:var(--muted);word-break:break-word}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px;text-transform:uppercase;
  letter-spacing:.04em}
td.mono,code,pre{font-family:var(--mono)}
code{background:var(--code);padding:1.5px 5px;border-radius:5px;font-size:13px}
pre{background:var(--code);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px;overflow-x:auto;font-size:13px;line-height:1.5}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
section{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px 20px;margin-bottom:16px}
section h2{margin:0 0 12px;font-size:15px;letter-spacing:-.01em}
section h2 .sub{font-weight:400;color:var(--muted);font-size:13px;margin-left:8px}
.required{border-left:3px solid var(--accent)}
#out{font-family:var(--code-font);font-size:26px;letter-spacing:.16em;text-align:center;
  padding:20px 14px;font-variant-ligatures:none;font-feature-settings:"ss01","zero","calt" 0;
  word-break:break-all;line-height:1.4}
.note.ok{border-left-color:var(--accent)}
.note.warn{border-left-color:var(--warn)}
button{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;
  padding:8px 14px;font:inherit;font-size:14px;cursor:pointer}
footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--muted);font-size:13px}
.empty{color:var(--muted);padding:28px 0}
h1.title{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}
"""

FILTER_JS = """
(function(){
  var q=document.getElementById('q'), k=document.getElementById('k'), s=document.getElementById('s');
  if(!q) return;
  var cards=[].slice.call(document.querySelectorAll('.card'));
  function apply(){
    var t=q.value.toLowerCase(), kk=k.value, ss=s.value, shown=0;
    cards.forEach(function(c){
    var statuses=(c.dataset.statuses||"").split(" ");
    var ok=(!t||c.dataset.search.indexOf(t)>-1)&&(!kk||c.dataset.kind===kk)&&(!ss||statuses.indexOf(ss)>-1);
      c.style.display=ok?'':'none'; if(ok)shown++;
    });
    document.getElementById('count').textContent=shown;
  }
  [q,k,s].forEach(function(el){el.addEventListener('input',apply)});
})();
"""


def shell(title: str, body: str, depth: int = 0) -> str:
  root = "../" * depth
  if API_BASE:
    api_link = f'<a href="{html.escape(API_BASE, quote=True)}/docs">API</a>'
  elif BLOB_BACKEND == "local":
    api_link = f'<a href="{root}docs">API</a>'
  else:
    api_link = ""
  return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head><body><div class="wrap">
<header class="top">
  <h1><a href="{root}index.html">openmodels</a></h1>
  <p>A public archive of openpilot driving models, indexed automatically from
     <code>commaai/openpilot</code> git history.</p>
  <nav class="top">
    <a href="{root}index.html">Models</a>
    <a href="{root}compose.html">Compose</a>
    <a href="{root}integrate.html">Integrate</a>
    {api_link}
    <a href="https://github.com/commaai/openpilot">openpilot</a>
  </nav>
</header>
{body}
<footer>
  <p>{DISCLAIMER}</p>
  <p>Models &copy; comma.ai, distributed under the
     <a href="https://github.com/commaai/openpilot/blob/master/LICENSE">openpilot MIT license</a>
     and sourced from <a href="https://github.com/commaai/openpilot">commaai/openpilot</a>.
     Not affiliated with or endorsed by comma.ai.
     For subjective comparisons of how models drive, see
     <a href="https://sunnylink.wiki/models">sunnylink.wiki</a>.</p>
</footer>
</div></body></html>
"""


def human_bytes(n: int) -> str:
  return f"{n/2**20:.0f} MB" if n < 2**30 else f"{n/2**30:.2f} GB"


def ago(iso: str) -> str:
  try:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
  except ValueError:
    return iso
  delta = datetime.now(timezone.utc) - then
  hours = delta.total_seconds() / 3600
  if hours < 1:
    return f"{int(delta.total_seconds()/60)} min ago"
  if hours < 48:
    return f"{int(hours)} h ago"
  return f"{delta.days} days ago"


def statuses(bundle: dict[str, Any]) -> list[str]:
  return sorted({o["status"] for o in bundle["occurrences"]})


ROLE_LABELS = {"vision": "vision", "on_policy": "on-policy", "off_policy": "off-policy",
               "supercombo": "combined supercombo", "dmonitoring": "driver monitoring",
               "navmodel": "navigation"}

# comma titles most model PRs ("Firehose model", "Tomb Raider 14"), but some land under the bare
# training-run reference, which renders as a card titled with a UUID.
CHECKPOINT_NAME = re.compile(
  r"^([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:/(\d+))?$", re.I)


def role_label(role: str) -> str:
  return ROLE_LABELS.get(role, role)


def plain_roles(bundle: dict[str, Any]) -> str:
  return " · ".join(role_label(f["role"]) for f in bundle["files"])


def pretty_name(bundle: dict[str, Any]) -> str:
  """Display title. Rephrases an opaque upstream name; never invents a description.

  The only rewrite is the training-run reference comma uses as a PR title — the same
  `<checkpoint>/<step>` form recorded in model metadata — so this stays a restatement of an
  upstream fact rather than a characterisation of the model.
  """
  if match := CHECKPOINT_NAME.match(bundle["name"]):
    step = f" · step {match.group(2)}" if match.group(2) else ""
    return f"Training run {match.group(1)}{step}"
  return bundle["name"]


def render_browse(index: dict[str, Any]) -> str:
  bundles = sorted(index["bundles"], key=lambda b: b["introduced_by"]["date"], reverse=True)
  cards = []
  for b in bundles:
    bundle_statuses = statuses(b)
    size = sum(f["size"] for f in b["files"])
    search = (f"{pretty_name(b)} {b['name']} {b['slug']} {b['bundle_id']} "
              f"{plain_roles(b)}").lower()
    head = '<span class="badge head">in HEAD</span>' if b["in_head"] else ""
    archived = ('<span class="badge pr_only">upstream ref gone</span>'
                if not b.get("upstream_reachable", True) else "")
    pr = b["introduced_by"]["pr"]
    pr_link = (f' · <a href="https://github.com/commaai/openpilot/pull/{pr}">#{pr}</a>'
               if pr else "")
    badges = "".join(f'<span class="badge {s}">{s.replace("_", " ")}</span>'
                      for s in bundle_statuses)
    cards.append(f"""<article class="card" data-kind="{b['kind']}"
   data-statuses="{' '.join(bundle_statuses)}"
   data-search="{html.escape(search, quote=True)}">
  <h3><a href="models/{b['bundle_id']}.html">{html.escape(pretty_name(b))}</a></h3>
  <div class="meta"><span>{b['introduced_by']['date'][:10]}</span><span>{human_bytes(size)}</span>
    <span>{b['kind']}</span></div>
  <div class="badges">{badges}<span class="badge">{b['family']}</span>
    <span class="badge">{b['variant']}</span>{head}{archived}</div>
  <div class="feat">{html.escape(plain_roles(b))}</div>
  <div class="meta">{b['bundle_id']}{pr_link}</div>
</article>""")

  kinds = sorted({b["kind"] for b in bundles})
  all_statuses = sorted({s for b in bundles for s in statuses(b)})
  body = f"""
<p class="note">{DISCLAIMER} Each entry links to the exact upstream commit so you can verify
   every claim yourself.</p>
<div class="controls">
  <input id="q" type="search" placeholder="Search {len(bundles)} models by name or id…"
         aria-label="Search models">
  <select id="k" aria-label="Filter by kind"><option value="">All kinds</option>
    {''.join(f'<option value="{k}">{k}</option>' for k in kinds)}</select>
  <select id="s" aria-label="Filter by status"><option value="">Any status</option>
    {''.join(f'<option value="{s}">{s.replace("_"," ")}</option>' for s in all_statuses)}</select>
</div>
<p class="meta"><span id="count">{len(bundles)}</span> of {len(bundles)} models ·
   indexed {ago(index['generated_at'])}</p>
<div class="grid">{''.join(cards)}</div>
<script>{FILTER_JS}</script>
"""
  return shell("openmodels — openpilot model archive", body)


def render_detail(index: dict[str, Any], bundle: dict[str, Any]) -> str:
  bundle_statuses = statuses(bundle)
  status_badges = "".join(
    f'<span class="badge {s}">{s.replace("_", " ")}</span>' for s in bundle_statuses
  )
  constants = bundle.get("host_constants") or {}
  sources = bundle.get("host_constants_sources") or {}
  missing = bundle.get("host_constants_missing") or []

  if constants:
    rows = "".join(
      f"<tr><td class='mono'>{html.escape(k)}</td>"
      f"<td class='mono'>{html.escape(str(v))}</td>"
      f"<td class='mono'>{html.escape(str(sources.get(k,'—')))}</td></tr>"
      for k, v in sorted(constants.items())
    )
    consts_html = f"""<div class="scroll"><table>
      <tr><th>constant</th><th>value</th><th>source</th></tr>{rows}</table></div>"""
  else:
    consts_html = ("<p class='empty'>No host constants recorded at this commit — they did not "
                   "exist in this era of openpilot.</p>")
  if missing:
    consts_html += (f"<p class='meta'>Not found at this commit: "
                    f"<code>{html.escape(', '.join(missing))}</code>. Reported as absent rather "
                    f"than defaulted &mdash; a wrong smoothing constant silently changes "
                    f"steering.</p>")

  records = {f["oid"]: f for f in index.get("files", [])}
  unavailable = set(index.get("mirror_unavailable", []))

  def download_href(member: dict[str, Any]) -> str | None:
    if BLOB_BACKEND == "local":
      return f"../blobs/{member['oid']}.onnx" if records.get(
        member["oid"], {}).get("local_mirrored") else None
    record = records.get(member["oid"], {})
    release, repo = record.get("release"), index.get("release_repo", "")
    if release and repo:
      return f"https://github.com/{repo}/releases/download/{release}/{member['oid']}.onnx"
    return None

  def download_cell(member: dict[str, Any]) -> str:
    if url := download_href(member):
      return f'<a href="{html.escape(url, quote=True)}">download</a>'
    return "unavailable upstream" if member["oid"] in unavailable else "mirror pending"

  files = "".join(
    f"<tr><td>{html.escape(role_label(f['role']))}</td>"
    f"<td class='mono'>{html.escape(f['filename'])}</td>"
    f"<td>{human_bytes(f['size'])}</td>"
    f"<td class='mono'>{f['oid'][:16]}…</td><td>{download_cell(f)}</td></tr>"
    for f in bundle["files"]
  )

  occ = "".join(
    f"<tr><td>{o['date'][:10]}</td>"
    f"<td><span class='badge {o['status']}'>{o['status'].replace('_',' ')}</span></td>"
    f"<td class='mono'><a href='https://github.com/commaai/openpilot/commit/{o['commit']}'>"
    f"{o['commit'][:10]}</a></td>"
    f"<td>{('<a href=https://github.com/commaai/openpilot/pull/%d>#%d</a>' % (o['pr'], o['pr'])) if o['pr'] else '—'}</td>"
    f"<td>{html.escape(o['subject'][:70])}</td></tr>"
    for o in sorted(bundle["occurrences"], key=lambda o: o["date"], reverse=True)
  )

  verify = "\n".join(
    ((f"curl -L '{download_href(f)}' -o {f['filename']}\n"
      f"echo '{f['oid']}  {f['filename']}' | sha256sum -c   # must pass")
     if download_href(f) else f"# {f['filename']}: not currently downloadable")
    for f in bundle["files"]
  )

  # Lineage: which training runs produced these halves, and what else they shipped against.
  pairings = index.get("attested_pairings", [])
  files_by_oid = {f["oid"]: f for f in index.get("files", [])}
  lineage_rows = []
  for member in bundle["files"]:
    meta = (files_by_oid.get(member["oid"], {}).get("metadata") or {})
    lin = meta.get("lineage")
    if not lin:
      continue
    ckpt = lin.get("self") or lin.get("vision")
    partners = sorted({p[1] for p in pairings if p[0] == ckpt}) if ckpt else []
    lineage_rows.append(
      f"<tr><td>{html.escape(role_label(member['role']))}</td>"
      f"<td class='mono'>{html.escape(str(ckpt))}</td>"
      f"<td>{('also shipped with %d other %s' % (len(partners), 'policy' if len(partners)==1 else 'policies')) if partners else '—'}</td></tr>"
    )
  lineage_html = ""
  if lineage_rows:
    lineage_html = f"""
<section>
  <h2>Lineage<span class="sub">which training runs produced these halves</span></h2>
  <p class="meta">comma records the training checkpoint in each model, and a fused supercombo
    names both of its halves. That makes "these were built for each other" a fact rather than a
    guess &mdash; and it is the only sound basis for combining halves, because the latent between
    vision and policy is untyped and will accept anything of the right width.</p>
  <div class="scroll"><table>
    <tr><th>role</th><th>checkpoint</th><th>attested partners</th></tr>{''.join(lineage_rows)}
  </table></div>
  <p class="meta">Combine halves with <code>POST /v1/compose</code>. A pairing without recorded
    attestation is returned with a cross-lineage caution: it will load and run either way.</p>
</section>"""

  body = f"""
<h1 class="title">{html.escape(pretty_name(bundle))}</h1>
<p class="meta">{status_badges}
  <span class="badge">{bundle['kind']}</span><span class="badge">{bundle['family']}</span>
  <span class="badge">{bundle['variant']}</span>
  <code>{bundle['bundle_id']}</code></p>

<section class="required">
  <h2>Required companion configuration<span class="sub">not optional</span></h2>
  <p class="meta">These host constants were in effect where this model ran upstream. comma
     changes them <em>in the same commit that swaps a model</em>, so weights without them are an
     incomplete artifact. A fork that pulls these weights and keeps its old constants gets a
     model that loads cleanly and steers differently than intended.</p>
  {consts_html}
  {'<p class="meta">Derived frame skip: <code>%s</code> (MODEL_RUN_FREQ // MODEL_CONTEXT_FREQ)</p>' % bundle['frame_skip'] if bundle.get('frame_skip') is not None else ''}
</section>

<section>
  <h2>Provenance<span class="sub">every field independently verifiable</span></h2>
  <p class="meta">Introduced by
    <a href="https://github.com/commaai/openpilot/commit/{bundle['introduced_by']['commit']}">
    <code>{bundle['introduced_by']['commit'][:10]}</code></a>
    on {bundle['introduced_by']['date'][:10]}. Present in upstream HEAD:
    <strong>{'yes' if bundle['in_head'] else 'no'}</strong>.</p>
  <div class="scroll"><table>
    <tr><th>date</th><th>status</th><th>commit</th><th>pr</th><th>subject</th></tr>{occ}
  </table></div>
</section>

{lineage_html}

<section>
  <h2>Files<span class="sub">content-addressed</span></h2>
  <div class="scroll"><table>
    <tr><th>role</th><th>filename</th><th>size</th><th>sha256 (oid)</th><th></th></tr>{files}
  </table></div>
  <p class="meta">Verify every download &mdash; the oid <em>is</em> the sha256:</p>
  <pre>{html.escape(verify)}</pre>
</section>

<section>
  <h2>What this page does not tell you</h2>
  <p class="meta">That this model is safe, or that it is interchangeable with any other model
    in this archive. Two models can share tensor shapes and slice widths while interpreting
    those numbers completely differently &mdash; column meanings inside a slice, MDN field
    ordering, and input normalization are all invisible here. Qualification is the fork's job.
    See <a href="../integrate.html">Integrate</a>.</p>
</section>
"""
  return shell(f"{pretty_name(bundle)} — openmodels", body, depth=1)


INTEGRATE = """
<h1 class="title">Integrating openmodels into a fork</h1>
<p class="note">Read this before wiring the API into anything that drives. The registry hands you
   <strong>source weights and provenance</strong>. Turning that into something a car runs is your
   fork's job, and so is deciding whether it is safe. API examples assume
   <code>API=https://models.example.com</code>; the reference client needs only the public static
   mirror.</p>

<section class="required">
  <h2>Start here: use the reference client</h2>
  <p class="meta">Everything below is what <code>clients/reference.py</code> already does
     correctly &mdash; oid verification, visible withdrawal status, and direct downloads from the
     recorded release. Copy it into your fork rather than hand-rolling
     curl. The rest of this page explains what it is doing and why.</p>
  <pre>python clients/reference.py --list
python clients/reference.py --pull &lt;bundle_id&gt; --out selfdrive/modeld/models</pre>
</section>

<section>
  <h2>1. Pick a model by provenance, not by shape</h2>
  <p class="meta">Ask what configuration a model ran in, then compare it to your own:</p>
  <pre>curl -s "$API/v1/models/&lt;bundle_id&gt;/provenance"</pre>
  <p class="meta">You get the upstream commit, the host constants in effect there, and every
     occurrence with its status. There is deliberately no endpoint that tells you two models are
     compatible &mdash; that claim cannot be made from the available data. Check
     <code>/v1/status</code> for catalog freshness; a stale <code>generated_at</code> means the
     indexer has stopped, which otherwise looks identical to "upstream shipped nothing".</p>
</section>

<section>
  <h2>2. Download every file in the bundle, and verify each one</h2>
  <p class="meta"><strong>A bundle is usually more than one file.</strong> A split model needs its
     vision <em>and</em> policy halves together &mdash; vision alone has no
     <code>plan</code>, <code>lead</code>, or <code>lane_lines</code> and cannot drive. Keep the
     filenames from <code>/provenance</code>; the build looks them up by name.</p>
  <p class="meta">The oid is the sha256. Refuse any blob that does not match &mdash; that check is
     the only thing standing between your fork and a swapped artifact.</p>
  <pre>curl -s "$API/v1/models/&lt;bundle_id&gt;/provenance" \\
  | jq -r '.files[] | "\\(.oid) \\(.filename)"' \\
  | while read oid name; do
      curl -sL "$API/v1/files/$oid/download" -o "$name"
      echo "$oid  $name" | sha256sum -c || exit 1
    done</pre>
  <p class="meta">A <code>503</code> means the blob is indexed but not yet mirrored &mdash; retry
     later. A <code>410</code> means the upstream LFS object disappeared before it could be
     archived. Files flagged <code>suspect</code> are upstream git
     conflict debris rather than models, and they mirror faithfully while being unusable.</p>
  <p class="meta"><strong>Map files to compiler inputs by <code>role</code>, never by
     filename.</strong> Names changed across eras &mdash; commit <code>249cafe</code> renamed
     <code>driving_policy.onnx</code> to <code>driving_on_policy.onnx</code> with identical
     content &mdash; so an older bundle's <code>on_policy</code> role still arrives as
     <code>driving_policy.onnx</code>. The role is stable; the filename is not.</p>
</section>

<section class="required">
  <h2>3. Apply the host constants</h2>
  <p class="meta">Set <code>LAT_SMOOTH_SECONDS</code> and <code>LONG_SMOOTH_SECONDS</code> to the
     values from <code>/provenance</code>. They feed lateral delay in controlsd, and comma
     changes them in the same commit that swaps a model. Shipping new weights against stale
     constants is the most likely way to get a model that appears to work and steers wrong.</p>
  <p class="meta"><strong>When <code>host_constants_missing</code> is non-empty, that is real
     information, not a gap to paper over.</strong> Older eras predate these constants entirely
     in some historical eras. The registry reports them absent
     rather than defaulting them to zero, because a wrong smoothing constant changes steering
     silently. If you cannot determine a value, treat the model as unqualified.</p>
</section>

<section>
  <h2>4. Compile for your target &mdash; and check the compiler accepts the architecture</h2>
  <p class="meta">openpilot builds the tinygrad pickle from ONNX during scons, choosing a backend
     from the hardware present. The QCOM path opens <code>/dev/kgsl-3d0</code> directly, so a
     device-usable artifact can only be produced on the device &mdash; not in CI.</p>
  <p class="meta"><strong>Upstream's compiler only accepts a vision + on-policy pair.</strong>
     <code>compile_modeld.py</code> requires <code>--vision-onnx</code> and
     <code>--on-policy-onnx</code>; there is no <code>--supercombo-onnx</code> and no off-policy
     input. Combined supercombos need a multi-era compiler such as sunnypilot's. Check the
     bundle's roles rather than relying on an era-wide count.</p>
  <p class="meta"><code>--frame-skip</code> is also required, and scons derives it from
     <em>your</em> <code>ModelConstants.MODEL_RUN_FREQ // MODEL_CONTEXT_FREQ</code> &mdash; it is
     a host property, not a model property. Provenance reports what the model ran with upstream,
     so a difference from your own value is a mismatch signal worth investigating rather than a
     number to copy blindly.</p>
  <pre>python selfdrive/modeld/compile_modeld.py \\
  --vision-onnx    selfdrive/modeld/models/driving_vision.onnx \\
  --on-policy-onnx selfdrive/modeld/models/driving_on_policy.onnx \\
  --model-size 512x256 --camera-resolutions 1928x1208 1344x760 \\
  --frame-skip 4 --output selfdrive/modeld/models/driving_tinygrad.pkl</pre>
  <p class="meta"><strong>Runtime model switching is where the cost lands.</strong> A settings-menu
     picker cannot re-run scons per selection, so it must invoke the compiler on-device:
     multi-minute, offroad-only, and it needs a progress UI, a disk budget, and a rollback path
     for a compile that fails or is interrupted.</p>
</section>

<section>
  <h2>5. Make withdrawal status impossible to miss</h2>
  <p class="meta">Filter to <code>status=merged</code> unless a human explicitly opted in. A
     reverted model is one comma pulled back, and "merged" is not the same as "approved" &mdash;
     several models landed and were reverted days later. PR-only models never cleared review at
     all. The reference client shows every status and offers <code>--merged-only</code> when a
     product chooses to hide withdrawn entries.</p>
  <pre>curl -s "$API/v1/models?status=merged&amp;kind=driving"</pre>
</section>

<section>
  <h2>What the registry never establishes</h2>
  <p class="meta">Target-hardware compilation, process replay, latency and memory behaviour,
     closed-loop driving quality, or safety. Everything served here is storage integrity and
     upstream history. Two models can share every tensor shape while interpreting those numbers
     completely differently, which is why no endpoint will ever tell you they are
     interchangeable. Qualify a model on your own hardware before anyone drives on it.</p>
</section>
"""


COMPOSE_JS = """
(function(){
  var D=null;
  // Mirrors index/code.py -- shapes are append-only and roles are the encoding order.
  var SHAPES=[["vision","on_policy"],["vision","on_policy","off_policy"],["supercombo"],
              ["vision","off_policy"],["big_vision","big_on_policy"],
              ["big_vision","big_on_policy","big_off_policy"],["big_supercombo"],
              ["dmonitoring"],["navmodel"]];
  var VERSION=3;

  // Files carry no date or name of their own; both come from the newest bundle shipping each oid.
  // Merged bundles carry comma's PR title ("Nicki Minaj Model"); PR-only ones carry the bare
  // training-run reference, which renders as the run/step a human recognises.
  var NEWEST={}, NAME={};
  function prettyName(n){
    var m=/^([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\\/(\\d+))?$/i.exec(n||"");
    if(!m) return n||"";
    return "Training run "+m[1]+(m[2]?" \\u00b7 step "+m[2]:"");
  }
  function dateOf(f){ return NEWEST[f.oid]||""; }
  function nameOf(f){ return NAME[f.oid]||(f.filenames[0]||"?"); }
  function ck(f){ var l=(f.metadata||{}).lineage||{}; return l.self||l.vision||null; }
  // The policy consumes the vision encoder's hidden state through features_buffer; its width is
  // the seam compose() checks. The vision side does not declare it, so only policies show it.
  function seam(f){ var s=(f.metadata||{}).input_shapes||{}; return (s.features_buffer||[])[2]||null; }
  // big_ is a bundle-level hardware variant (USBGPU/AMD vs QCOM), not a role: the indexer reads
  // big_driving_vision.onnx as plain vision. Halves must agree on it or nothing can run them.
  function variantOf(f){
    return (f.filenames||[]).some(function(n){ return n.indexOf("big_")===0; })
      ? "big" : "standard";
  }
  // Mirrors index/code.py. Base26 over an alphabet with no confusable characters, so a
  // misread self-corrects on entry rather than merely failing.
  var ALPHABET="0123456789ACDEFHJKMNPRVWXY";
  function b26(bytes, chars){
    var v=0n;
    for(var i=0;i<bytes.length;i++) v=(v<<8n)|BigInt(bytes[i]);
    var out="", B=BigInt(ALPHABET.length);
    for(var j=0;j<chars;j++){ out=ALPHABET[Number(v%B)]+out; v=v/B; }
    return out;
  }
  function charsFor(n){ return Math.ceil(n*8/Math.log2(ALPHABET.length)); }
  async function sha256(str){
    var buf=await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf));
  }
  function shapeOf(roles){
    for(var i=0;i<SHAPES.length;i++){
      if(SHAPES[i].length===roles.length &&
         SHAPES[i].every(function(r){ return roles.indexOf(r)>-1; })) return i;
    }
    return -1;
  }
  async function makeCode(sel){
    var roles=Object.keys(sel), si=shapeOf(roles);
    if(si<0) return "(no code shape for this combination)";
    var body=[(VERSION<<5)|si];
    SHAPES[si].forEach(function(r){
      var hex=sel[r].slice(0,6);
      for(var i=0;i<6;i+=2) body.push(parseInt(hex.substr(i,2),16));
    });
    var sorted=roles.slice().sort();
    var digest=await sha256(sorted.map(function(r){return r+":"+sel[r];}).join(""));
    body=body.concat(digest.slice(0,1));
    var t=b26(body, charsFor(body.length)), g=[];
    for(var i=0;i<t.length;i+=4) g.push(t.slice(i,i+4));
    return "OM3-"+g.join("-");
  }

  function options(role){
    return (D.files||[]).filter(function(f){
      return (f.filenames||[]).some(function(n){
        return role==="vision" ? n.indexOf("vision")>-1
             : n.indexOf("policy")>-1 && policyRole(f)===role; });
    });
  }
  // off_policy is a distinct role in the index, not a flavour of on_policy. Encoding one as the
  // other would make the code name weights the user did not pick -- the exact misresolution the
  // format exists to prevent -- so the role travels with the option.
  function policyRole(f){
    return (f.filenames||[]).some(function(n){ return n.indexOf("off_policy")>-1; })
      ? "off_policy" : "on_policy";
  }
  function attested(vc, pc){
    return !!(vc&&pc) && (D.attested_pairings||[]).some(function(pr){
      return pr[0]===vc && pr[1]===pc; });
  }
  // Fill a select. variant/attest constrain the options; each select is role-fixed, so the
  // role never has to be detected from the pick. Attested options sort first and carry a check.
  var FILTERS={};
  function applyFilter(id){
    var t=(FILTERS[id]||"").toLowerCase(), el=document.getElementById(id);
    Array.prototype.forEach.call(el.options, function(o){
      if(!o.value) return;
      o.hidden = o.textContent.toLowerCase().indexOf(t)<0;
    });
  }
  // With the checkbox on, policy options that were never attested with the picked vision hide.
  function applyAttestedFilter(){
    if(!document.getElementById("attestedOnly").checked) return;
    var v=document.getElementById("vsel").value;
    var vc=v?ck(byOid[v]):null;
    ["psel","psel2"].forEach(function(id){
      var el=document.getElementById(id);
      Array.prototype.forEach.call(el.options, function(o){
        if(!o.value) return;
        o.hidden = !(vc&&attested(vc,ck(byOid[o.value])));
      });
    });
  }
  function fill(id, role, variant, vc){
    var el=document.getElementById(id); el.innerHTML="";
    options(role).filter(function(f){
      if(variant && variantOf(f)!==variant) return false;
      return true; })
    .sort(function(a,b){
      var da=dateOf(a), db=dateOf(b);
      if(vc){ var aa=attested(vc,ck(a))?1:0, ab=attested(vc,ck(b))?1:0; if(aa!==ab) return ab-aa; }
      return da<db?1:da>db?-1:0; })
    .forEach(function(f){
      var o=document.createElement("option");
      o.value=f.oid;
      var mark = vc&&attested(vc,ck(f)) ? "\\u2713 attested  " : "";
      o.textContent=mark+nameOf(f)+"  "+f.filenames[0]+"  "+
                    (dateOf(f).slice(0,10)||"undated")+"  "+Math.round(f.size/1048576)+" MB";
      el.appendChild(o);
    });
    if(FILTERS[id]) applyFilter(id);
    if(role!=="vision") applyAttestedFilter();
  }
  // A bundle is a quick pick when every vision/policy pair in it is attested: it is a
  // combination comma actually shipped, so it is the honest starting point for composing.
  function attestedBundle(b){
    var vc=null, pols=[];
    b.files.forEach(function(m){
      var f=byOid[m.oid]; if(!f) return;
      if(m.role==="vision") vc=ck(f); else pols.push(ck(f));
    });
    return !!(vc&&pols.length) && pols.every(function(pc){ return attested(vc,pc); });
  }
  function selectQuick(b){
    var byRole={}; b.files.forEach(function(m){ byRole[m.role]=m.oid; });
    var vsel=document.getElementById("vsel"), psel=document.getElementById("psel"),
        psel2=document.getElementById("psel2");
    vsel.value=byRole.vision||"";
    var vv=vsel.value?variantOf(byOid[vsel.value]):null, vc=vsel.value?ck(byOid[vsel.value]):null;
    fill("psel","on_policy",vv,vc);
    psel.value=byRole.on_policy||"";
    fill("psel2","off_policy",vv,vc);
    psel2.value=byRole.off_policy||"";
    update();
  }
  async function update(){
    var vsel=document.getElementById("vsel"), psel=document.getElementById("psel"),
        psel2=document.getElementById("psel2");
    var out=document.getElementById("out"), note=document.getElementById("note"),
        man=document.getElementById("manifest");
    var v=vsel.value, p=psel.value, p2=psel2.value;
    if(!v||!p){ out.textContent="Pick a vision half and an on-policy half.";
      note.textContent=""; man.innerHTML=""; return; }
    var vf=byOid[v], pf=byOid[p];
    if(variantOf(vf)!==variantOf(pf)||(p2&&variantOf(byOid[p2])!==variantOf(vf))){
      out.textContent=""; man.innerHTML="";
      note.className="note warn";
      note.innerHTML="<strong>Halves disagree on hardware target.</strong> "+variantOf(vf)+
        " and "+variantOf(pf)+" run on different devices (QCOM vs USBGPU/AMD) \\u2014 compose refuses this combination.";
      return;
    }
    var sel={vision:v, on_policy:p};
    if(p2) sel.off_policy=p2;
    var vc=ck(vf), pc1=ck(pf), pc2=p2?ck(byOid[p2]):null;
    var unknown=!vc||!pc1||(p2&&!pc2);
    var a1=attested(vc,pc1), a2=p2?attested(vc,pc2):null;
    note.className = unknown||(!p2&&!a1)||(p2&&!(a1&&a2)) ? "note warn" : "note ok";
    if(unknown)
      note.innerHTML="<strong>Lineage unknown</strong> for one half \\u2014 whether these were built for each other cannot be determined.";
    else if(p2&&a1&&a2)
      note.innerHTML="<strong>Attested pairing.</strong> All three halves shipped together upstream.";
    else if(p2&&a1)
      note.innerHTML="<strong>on-policy attested</strong> with this vision; the off-policy half is <strong>cross-lineage</strong>.";
    else if(p2&&a2)
      note.innerHTML="<strong>off-policy attested</strong> with this vision; the on-policy half is <strong>cross-lineage</strong>.";
    else if(!p2&&a1)
      note.innerHTML="<strong>Attested pairing.</strong> These halves shipped together upstream.";
    else
      note.innerHTML="<strong>Cross-lineage.</strong> No shipped pairing is recorded in this catalog. The latent between them is untyped, so this will load and run regardless of whether the numbers mean the same thing.";
    var rows="";
    Object.keys(sel).forEach(function(r){
      var f=byOid[sel[r]], s=seam(f);
      rows+="<tr><td>"+r+"</td><td class='mono'>"+f.filenames[0]+"</td><td>"+
            Math.round(f.size/1048576)+" MB</td><td class='mono'>"+(ck(f)||"\\u2014")+
            "</td><td>"+(s?s:"\\u2014")+"</td></tr>";
    });
    man.innerHTML="<table><thead><tr><th>role</th><th>file</th><th>size</th>"+
      "<th>checkpoint</th><th>seam width</th></tr></thead><tbody>"+rows+"</tbody></table>";
    out.textContent = await makeCode(sel);
  }
  var byOid={};
  fetch("index.json").then(function(r){return r.json();}).then(function(d){
    D=d;
    (D.bundles||[]).forEach(function(b){
      var dt=(b.introduced_by||{}).date||"";
      (b.files||[]).forEach(function(m){
        if(dt>(NEWEST[m.oid]||"")){ NEWEST[m.oid]=dt; NAME[m.oid]=prettyName(b.name||""); }
      });
    });
    (D.files||[]).forEach(function(f){ byOid[f.oid]=f; });
    fill("vsel","vision");
    fill("psel","on_policy");
    fill("psel2","off_policy");
    // Guided entry: the newest attested bundles, one click fills all three halves.
    var quick=(D.bundles||[]).slice().sort(function(a,b){
      return (a.introduced_by||{}).date<(b.introduced_by||{}).date?1:-1; })
      .filter(attestedBundle).slice(0,4);
    var qel=document.getElementById("quick");
    quick.forEach(function(b){
      var btn=document.createElement("button");
      btn.type="button";
      btn.textContent=prettyName(b.name||"")+"  "+(b.introduced_by||{}).date.slice(0,10);
      btn.addEventListener("click", function(){ selectQuick(b); });
      qel.appendChild(btn);
    });
    [["vfilter","vsel"],["pfilter","psel"],["p2filter","psel2"]].forEach(function(pair){
      document.getElementById(pair[0]).addEventListener("input", function(){
        FILTERS[pair[1]]=this.value; applyFilter(pair[1]);
      });
    });
    document.getElementById("attestedOnly").addEventListener("change", applyAttestedFilter);
    document.getElementById("vsel").addEventListener("change", function(){
      var v=document.getElementById("vsel").value;
      var vv=v?variantOf(byOid[v]):null, vc=v?ck(byOid[v]):null;
      fill("psel","on_policy",vv,vc);
      fill("psel2","off_policy",vv,vc);
      update();
    });
    document.getElementById("psel").addEventListener("change", update);
    document.getElementById("psel2").addEventListener("change", update);
    update();
  }).catch(function(e){
    document.getElementById("out").textContent="could not load catalog: "+e;
  });
  document.getElementById("copy").addEventListener("click", function(){
    navigator.clipboard.writeText(document.getElementById("out").textContent);
  });
})();
"""


COMPOSE = """
<h1 class="title">Compose a model</h1>
<p class="note">Combine a vision half with an on-policy half &mdash; and optionally an off-policy
   half alongside it &mdash; and get a code you can paste into a model picker. The on-policy half
   carries the control outputs and is what compiles on device; the off-policy half carries the
   full plan and is the optional extra comma trains alongside it. But <strong>the exact
   combination you build here has never been driven</strong>.</p>

<section>
  <h2>Pick the halves</h2>
  <p class="meta">Start from the newest combinations that shipped together upstream:</p>
  <div id="quick" class="controls" aria-label="attested quick picks"></div>
  <div class="controls">
    <div class="pick">
      <input id="vfilter" type="search" placeholder="filter vision halves\u2026"
             aria-label="Filter vision halves">
      <select id="vsel" aria-label="vision half (required)">
        <option value="">vision half (required)\u2026</option></select>
    </div>
    <div class="pick">
      <input id="pfilter" type="search" placeholder="filter on-policy halves\u2026"
             aria-label="Filter on-policy halves">
      <select id="psel" aria-label="on-policy half (required)">
        <option value="">on-policy half (required)\u2026</option></select>
    </div>
    <div class="pick">
      <input id="p2filter" type="search" placeholder="filter off-policy halves\u2026"
             aria-label="Filter off-policy halves">
      <select id="psel2" aria-label="off-policy half (optional)">
        <option value="">off-policy half (optional)\u2026</option></select>
    </div>
  </div>
  <label class="meta"><input id="attestedOnly" type="checkbox">
    only show policies attested with the picked vision</label>
  <p id="note" class="meta"></p>
</section>

<section class="required">
  <h2>Your code<span class="sub">paste this into a picker</span></h2>
  <div id="manifest" class="scroll"></div>
  <pre id="out">Pick a vision half and an on-policy half.</pre>
  <button id="copy" class="controls">Copy</button>
  <p class="meta">14 characters &mdash; the <code>OM3-</code> prefix is for recognition and does
     not need typing. The code carries which files you picked, not a promise about them. Redeeming it
     &mdash; <code>GET /v1/compose/&lt;code&gt;</code>, or <code>redeem_code()</code> in the
     runtime library &mdash; resolves it against the catalog and re-runs every check. A damaged
     code fails to resolve rather than quietly naming different weights.</p>
</section>

<section>
  <h2>Before you drive it</h2>
  <p class="meta">Structural checks passing is not a safety result. The halves may carry
     different host constants, since they came from different commits &mdash; redeem the code to
     see both and choose deliberately. Then compile on-device and qualify it yourself.</p>
</section>
"""


def write_atomic(path: Path, content: str) -> None:
  tmp = path.with_suffix(path.suffix + ".tmp")
  tmp.write_text(content)
  tmp.replace(path)


def render(index_path: Path, out_dir: Path) -> int:
  index = json.loads(index_path.read_text())
  (out_dir / "models").mkdir(parents=True, exist_ok=True)

  write_atomic(out_dir / "integrate.html", shell("Integrate — openmodels", INTEGRATE))
  write_atomic(out_dir / "compose.html",
               shell("Compose — openmodels", COMPOSE + f"<script>{COMPOSE_JS}</script>"))
  for bundle in index["bundles"]:
    write_atomic(out_dir / "models" / f"{bundle['bundle_id']}.html",
                 render_detail(index, bundle))

  # Publish discovery surfaces last: every detail page they name already exists.
  write_atomic(out_dir / "index.json", json.dumps(index))
  write_atomic(out_dir / "index.html", render_browse(index))
  write_atomic(out_dir / ".nojekyll", "")
  return len(index["bundles"]) + 2


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
