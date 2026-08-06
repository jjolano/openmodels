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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DISCLAIMER = (
  "This registry asserts <strong>blob identity and upstream provenance</strong>. It does not "
  "assert that a model is safe to drive, or that two models are interchangeable."
)

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#fbfbfa; --panel:#fff; --ink:#16150f; --muted:#6b6a63; --line:#e3e2dc;
  --accent:#3d5a3d; --warn:#8a5a1f; --danger:#8f3232; --code:#f4f4f1;
  --radius:10px; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;\n  --code-font:"JetBrains Mono","IBM Plex Mono","Roboto Mono","DejaVu Sans Mono",\n              ui-monospace,SFMono-Regular,Menlo,monospace;
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
  word-break:break-all;line-height:1.4}\n.note.ok{border-left-color:var(--accent)}\n.note.warn{border-left-color:var(--warn)}\nbutton{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 14px;font:inherit;font-size:14px;cursor:pointer}
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
      var ok=(!t||c.dataset.search.indexOf(t)>-1)&&(!kk||c.dataset.kind===kk)&&(!ss||c.dataset.status===ss);
      c.style.display=ok?'':'none'; if(ok)shown++;
    });
    document.getElementById('count').textContent=shown;
  }
  [q,k,s].forEach(function(el){el.addEventListener('input',apply)});
})();
"""


def shell(title: str, body: str, depth: int = 0) -> str:
  root = "../" * depth
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
    <a href="/docs">API</a>
    <a href="https://github.com/commaai/openpilot">openpilot</a>
  </nav>
</header>
{body}
<footer>
  <p>{DISCLAIMER}</p>
  <p>Models &copy; comma.ai, MIT licensed, sourced from
     <a href="https://github.com/commaai/openpilot">commaai/openpilot</a>.
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


def latest_status(bundle: dict[str, Any]) -> str:
  return max(bundle["occurrences"], key=lambda o: o["date"])["status"]


def plain_roles(bundle: dict[str, Any]) -> str:
  names = {"vision": "vision", "on_policy": "on-policy", "off_policy": "off-policy",
           "supercombo": "combined supercombo", "dmonitoring": "driver monitoring",
           "navmodel": "navigation"}
  return " · ".join(names.get(f["role"], f["role"]) for f in bundle["files"])


def render_browse(index: dict[str, Any]) -> str:
  bundles = sorted(index["bundles"], key=lambda b: b["introduced_by"]["date"], reverse=True)
  cards = []
  for b in bundles:
    status = latest_status(b)
    size = sum(f["size"] for f in b["files"])
    search = f"{b['name']} {b['slug']} {b['bundle_id']} {plain_roles(b)}".lower()
    head = '<span class="badge head">in HEAD</span>' if b["in_head"] else ""
    pr = b["introduced_by"]["pr"]
    pr_link = (f' · <a href="https://github.com/commaai/openpilot/pull/{pr}">#{pr}</a>'
               if pr else "")
    cards.append(f"""<article class="card" data-kind="{b['kind']}" data-status="{status}"
   data-search="{html.escape(search, quote=True)}">
  <h3><a href="models/{b['bundle_id']}.html">{html.escape(b['name'])}</a></h3>
  <div class="meta"><span>{b['introduced_by']['date'][:10]}</span><span>{human_bytes(size)}</span>
    <span>{b['kind']}</span></div>
  <div class="badges"><span class="badge {status}">{status.replace('_',' ')}</span>
    <span class="badge">{b['family']}</span><span class="badge">{b['variant']}</span>{head}</div>
  <div class="feat">{html.escape(plain_roles(b))}</div>
  <div class="meta">{b['bundle_id']}{pr_link}</div>
</article>""")

  kinds = sorted({b["kind"] for b in bundles})
  statuses = sorted({latest_status(b) for b in bundles})
  body = f"""
<p class="note">{DISCLAIMER} Each entry links to the exact upstream commit so you can verify
   every claim yourself.</p>
<div class="controls">
  <input id="q" type="search" placeholder="Search {len(bundles)} models by name or id…"
         aria-label="Search models">
  <select id="k" aria-label="Filter by kind"><option value="">All kinds</option>
    {''.join(f'<option value="{k}">{k}</option>' for k in kinds)}</select>
  <select id="s" aria-label="Filter by status"><option value="">Any status</option>
    {''.join(f'<option value="{s}">{s.replace("_"," ")}</option>' for s in statuses)}</select>
</div>
<p class="meta"><span id="count">{len(bundles)}</span> of {len(bundles)} models ·
   indexed {ago(index['generated_at'])}</p>
<div class="grid">{''.join(cards)}</div>
<script>{FILTER_JS}</script>
"""
  return shell("openmodels — openpilot model archive", body)


def render_detail(index: dict[str, Any], bundle: dict[str, Any]) -> str:
  status = latest_status(bundle)
  constants = bundle.get("host_constants") or {}
  sources = bundle.get("host_constants_sources") or {}
  missing = bundle.get("host_constants_missing") or []

  if constants:
    rows = "".join(
      f"<tr><td class='mono'>{html.escape(k)}</td><td class='mono'>{v}</td>"
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

  files = "".join(
    f"<tr><td>{html.escape(f['role'])}</td><td class='mono'>{html.escape(f['filename'])}</td>"
    f"<td>{human_bytes(f['size'])}</td>"
    f"<td class='mono'>{f['oid'][:16]}…</td>"
    f"<td><a href='/v1/files/{f['oid']}/download'>download</a></td></tr>"
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
    f"curl -L https://openmodels.example/v1/files/{f['oid']}/download -o {f['filename']}\n"
    f"echo '{f['oid']}  {f['filename']}' | sha256sum -c   # must pass"
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
      f"<tr><td>{html.escape(member['role'])}</td>"
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
  <p class="meta">Combine halves with <code>POST /v1/compose</code>. A pairing that never shipped
    upstream is returned with a cross-lineage caution: it will load and run either way.</p>
</section>"""

  body = f"""
<h1 class="title">{html.escape(bundle['name'])}</h1>
<p class="meta"><span class="badge {status}">{status.replace('_',' ')}</span>
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
  return shell(f"{bundle['name']} — openmodels", body, depth=1)


INTEGRATE = """
<h1 class="title">Integrating openmodels into a fork</h1>
<p class="note">Read this before wiring the API into anything that drives. The registry hands you
   <strong>source weights and provenance</strong>. Turning that into something a car runs is your
   fork's job, and so is deciding whether it is safe. Replace
   <code>openmodels.example</code> below with the instance you are using.</p>

<section class="required">
  <h2>Start here: use the reference client</h2>
  <p class="meta">Everything below is what <code>clients/reference.py</code> already does
     correctly &mdash; oid verification, default-denying withdrawn models, and falling back to
     the static mirror when the API is down. Copy it into your fork rather than hand-rolling
     curl. The rest of this page explains what it is doing and why.</p>
  <pre>python clients/reference.py --list
python clients/reference.py --pull &lt;bundle_id&gt; --out selfdrive/modeld/models</pre>
</section>

<section>
  <h2>1. Pick a model by provenance, not by shape</h2>
  <p class="meta">Ask what configuration a model ran in, then compare it to your own:</p>
  <pre>curl -s https://openmodels.example/v1/models/&lt;bundle_id&gt;/provenance</pre>
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
  <pre>curl -s https://openmodels.example/v1/models/&lt;bundle_id&gt;/provenance \\
  | jq -r '.files[] | "\\(.oid) \\(.filename)"' \\
  | while read oid name; do
      curl -sL "https://openmodels.example/v1/files/$oid/download" -o "$name"
      echo "$oid  $name" | sha256sum -c || exit 1
    done</pre>
  <p class="meta">A <code>503</code> means the blob is indexed but not yet mirrored &mdash; retry
     later. Two files in the archive are flagged <code>suspect</code>: they are upstream git
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
     &mdash; 41 of the 142 driving bundles here have none. The registry reports them absent
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
     input. Of the 142 driving bundles here, <strong>66 are vision+on_policy and compile with
     upstream today</strong>, while <strong>76 are combined supercombo</strong> and need a
     multi-era compiler such as sunnypilot's.</p>
  <p class="meta">In practice that split falls almost entirely along age. openpilot went
     supercombo &rarr; split &rarr; supercombo, so the format is both its oldest and its newest:
     <strong>75 of the 76 supercombo bundles are the legacy <code>supercombo.onnx</code>
     (2020&ndash;2024)</strong>, and the remaining one is the June 2026
     <code>driving_supercombo.onnx</code> from the combined-onnx change. <strong>Every driving
     model from 2025 onward is vision+on_policy</strong> and builds with stock tooling &mdash;
     the compiler gap is mostly historical archive, not models you would run. Check the
     bundle's roles regardless.</p>
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
  <h2>5. Default-deny what upstream withdrew</h2>
  <p class="meta">Filter to <code>status=merged</code> unless a human explicitly opted in. A
     reverted model is one comma pulled back, and "merged" is not the same as "approved" &mdash;
     several models landed and were reverted days later. PR-only models never cleared review at
     all. The reference client applies this by default; if you query directly, apply it
     yourself.</p>
  <pre>curl -s "https://openmodels.example/v1/models?status=merged&amp;kind=driving"</pre>
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
  var VERSION=2;

  function ck(f){ var l=(f.metadata||{}).lineage||{}; return l.self||l.vision||null; }
  function b32(bytes){
    var A="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567", out="", bits=0, val=0;
    for(var i=0;i<bytes.length;i++){ val=(val<<8)|bytes[i]; bits+=8;
      while(bits>=5){ out+=A[(val>>>(bits-5))&31]; bits-=5; } }
    if(bits>0) out+=A[(val<<(5-bits))&31];
    return out;
  }
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
    var t=b32(body), g=[];
    for(var i=0;i<t.length;i+=4) g.push(t.slice(i,i+4));
    return "OM2-"+g.join("-");
  }

  function options(role){
    return (D.files||[]).filter(function(f){
      return (f.filenames||[]).some(function(n){
        return role==="vision" ? n.indexOf("vision")>-1 && n.indexOf("big_")<0
             : n.indexOf("policy")>-1 && n.indexOf("big_")<0; });
    });
  }
  function fill(id, role){
    var el=document.getElementById(id);
    options(role).forEach(function(f){
      var o=document.createElement("option");
      o.value=f.oid;
      o.textContent=(f.filenames[0]||"?")+"  "+(ck(f)||"lineage unknown").slice(0,22)+
                    "  ("+Math.round(f.size/1048576)+" MB)";
      el.appendChild(o);
    });
  }
  async function update(){
    var v=document.getElementById("vsel").value, p=document.getElementById("psel").value;
    var out=document.getElementById("out"), note=document.getElementById("note");
    if(!v||!p){ out.textContent="Pick a vision half and a policy half."; note.textContent=""; return; }
    var byOid={}; (D.files||[]).forEach(function(f){ byOid[f.oid]=f; });
    var vc=ck(byOid[v]), pc=ck(byOid[p]);
    var attested=(D.attested_pairings||[]).some(function(pr){ return pr[0]===vc && pr[1]===pc; });
    note.className = attested ? "note ok" : "note warn";
    note.innerHTML = !vc||!pc
      ? "<strong>Lineage unknown</strong> for one half \\u2014 whether these were built for each other cannot be determined."
      : attested
        ? "<strong>Attested pairing.</strong> These halves shipped together upstream."
        : "<strong>Cross-lineage.</strong> These never shipped together. The latent between them is untyped, so this will load and run regardless of whether the numbers mean the same thing.";
    out.textContent = await makeCode({vision:v, on_policy:p});
  }
  fetch("index.json").then(function(r){return r.json();}).then(function(d){
    D=d; fill("vsel","vision"); fill("psel","policy");
    ["vsel","psel"].forEach(function(id){
      document.getElementById(id).addEventListener("change", update); });
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
<p class="note">Combine a vision half with a policy half and get a code you can paste into a
   model picker. comma ships one vision encoder against several policies, so this is how upstream
   already works &mdash; but <strong>the exact combination you build here has never been
   driven</strong>.</p>

<section>
  <h2>Pick the halves</h2>
  <div class="controls">
    <select id="vsel" aria-label="vision half"><option value="">vision half\u2026</option></select>
    <select id="psel" aria-label="policy half"><option value="">policy half\u2026</option></select>
  </div>
  <p id="note" class="meta"></p>
</section>

<section class="required">
  <h2>Your code<span class="sub">paste this into a picker</span></h2>
  <pre id="out">Pick a vision half and a policy half.</pre>
  <button id="copy" class="controls">Copy</button>
  <p class="meta">13 characters &mdash; the <code>OM2-</code> prefix is for recognition and does
     not need typing. The code carries which files you picked, not a promise about them. Redeeming it
     &mdash; <code>GET /v1/compose/&lt;code&gt;</code>, or <code>redeem_code()</code> in the
     runtime library &mdash; resolves it against the catalog and re-runs every check. A damaged
     code fails to resolve rather than quietly naming different weights.</p>
</section>

<section>
  <h2>Before you drive it</h2>
  <p class="meta">Structural checks passing is not a safety result. The two halves may carry
     different host constants, since they came from different commits &mdash; redeem the code to
     see both and choose deliberately. Then compile on-device and qualify it yourself.</p>
</section>
"""


def render(index_path: Path, out_dir: Path) -> int:
  index = json.loads(index_path.read_text())
  (out_dir / "models").mkdir(parents=True, exist_ok=True)

  (out_dir / "index.html").write_text(render_browse(index))
  (out_dir / "integrate.html").write_text(shell("Integrate — openmodels", INTEGRATE))
  (out_dir / "compose.html").write_text(
    shell("Compose — openmodels", COMPOSE + f"<script>{COMPOSE_JS}</script>"))
  for bundle in index["bundles"]:
    (out_dir / "models" / f"{bundle['bundle_id']}.html").write_text(render_detail(index, bundle))

  # index.json alongside the HTML: this tree is the CDN fallback the clients read.
  (out_dir / "index.json").write_text(json.dumps(index))
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
