"""Static model browsing and exact configuration downloads."""
from html import escape as e
from urllib.parse import urljoin, urlparse
from openmodels.contracts import sha256

NAME_KINDS = {"published": "Published name", "source": "Source-derived label", "generated": "Generated label"}


def filename(model):
  return 'model-' + sha256(model['id'].encode())[:24] + '.html'


STYLE = '''<style>
.hero{padding:4px 0 12px;max-width:760px}.hero h1{font-size:clamp(28px,4vw,40px);line-height:1.15;letter-spacing:-.04em;margin:0 0 10px}
.hero p{font-size:16px;color:var(--muted)}.model-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:18px;margin-top:24px}
.model-card{overflow-wrap:anywhere;padding:22px;background:var(--panel);border:1px solid var(--line);border-radius:12px}.model-card h2{font-size:21px;line-height:1.3;margin:12px 0}.model-card h2 a{text-decoration:none;color:var(--ink)}
.model-card p{margin:10px 0}.model-card a:last-child{display:inline-block;padding:10px 0}.filters{display:flex;gap:16px;flex-wrap:wrap;align-items:end}.filters label{display:grid;gap:6px;flex:1;min-width:160px}
input,select{font:inherit;padding:12px;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:8px}a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.variant{padding:20px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}.variant summary{cursor:pointer;padding:12px 0}pre{background:var(--code);padding:18px;overflow:auto}nav.top a{padding:8px 0;min-height:44px}
</style>'''
SCRIPT = '''<script>
const search=document.querySelector('#model-search'), kind=document.querySelector('#model-kind'), naming=document.querySelector('#model-naming');
function filter(){let count=0;document.querySelectorAll('[data-model]').forEach(row=>{row.hidden=!((row.textContent+row.dataset.aliases).toLowerCase().includes(search.value.toLowerCase())&&(!kind.value||row.dataset.kind===kind.value)&&(!naming.value||row.dataset.naming===naming.value));if(!row.hidden)count++;});document.querySelector('#model-count').textContent=count+(count===1?' model shown':' models shown');document.querySelector('#model-empty').hidden=count!==0;}
search.addEventListener('input',filter);kind.addEventListener('change',filter);naming.addEventListener('change',filter);
</script>'''


def listing(catalog, shell, *, archive=False):
  models = [m for m in catalog.models(include_archive=True) if not archive or m['archived']]
  cards = []
  for m in models:
    formats = sorted({f for v in m['variants'] for f in v['formats']})
    aliases = ', '.join(dict.fromkeys(n['name'] for n in m.get('names', []) if n['name'].casefold() != m['name'].casefold()))
    name_kind = m.get('name_kind', 'published')
    cards.append(f'''<article class="model-card" data-model data-kind="{e(m['kind'], quote=True)}" data-naming="{e(name_kind, quote=True)}" data-aliases="{e(aliases, quote=True)}">
      <div class="meta">{e(m['publisher'])} · {e(m['kind'])}</div><h2><a href="{filename(m)}">{e(m['name'])}</a></h2>
      <p class="meta">{NAME_KINDS[name_kind]}</p>
      {f'<p class="meta">Also listed as {e(aliases)}</p>' if aliases else ""}
      <p>{e(m['family'])} · {e(', '.join(formats).upper())}</p>
      <p class="meta">{len(m['variants'])} source configurations · {'Downloads available' if any(v['available'] for v in m['variants']) else 'Downloads unavailable'}</p>
      <a href="{filename(m)}">View model →</a></article>''')
  title = 'Historical models' if archive else 'Explore models'
  intro = 'Models outside the current upstream tree, including published names and training runs.' if archive else 'Every model and source configuration in the catalog. Search by published name, alias, or generated label.'
  options = ''.join(f'<option value="{e(k, quote=True)}">{e(k.capitalize())}</option>' for k in sorted({m['kind'] for m in models}))
  return shell(title + ' — openmodels', STYLE + f'''<main><section class="hero"><h1>{title}</h1><p>{intro}</p>
    </section>
    <div class="filters"><label for="model-search">Search models<input type="search" id="model-search" placeholder="Try Duck Amigo or North Dakota"></label>
    <label for="model-kind">Model type<select id="model-kind"><option value="">All types</option>{options}</select></label>
    <label for="model-naming">Naming<select id="model-naming"><option value="">All names and labels</option><option value="published">Published names</option><option value="source">Source-derived labels</option><option value="generated">Generated labels</option></select></label></div>
    <p id="model-count" role="status">{len(models)} models shown · named choices first, then newest source activity</p>
    <p id="model-empty" hidden>No models match. Clear your search or choose another type.</p>
    <div class="model-grid">{''.join(cards)}</div>
    <p>{'All models are in the <a href="index.html">model directory</a>.' if archive else 'Browse <a href="archive.html">historical models</a>. Generated labels identify weights without claiming a published nickname.'}</p></main>''' + SCRIPT)


def detail(catalog, model, shell, *, base_url=""):
  links = ''.join(f'<li><a href="{e(url, quote=True)}">Source {i + 1}</a></li>' for i, url in enumerate(model['links']) if urlparse(url).scheme in ('https', 'http'))
  claims = []
  for claim in model.get('names', []):
    evidence = f'<a href="{e(claim["url"], quote=True)}">Evidence</a>' if urlparse(claim['url']).scheme in ('https', 'http') else ''
    claims.append(f'<li><strong>{e(claim["name"])}</strong> — {e(claim["source"])} · {NAME_KINDS[claim["method"]]} {evidence}</li>')
  naming = '<h2>Names and aliases</h2><ul>' + ''.join(claims) + '</ul>' if claims else '<p>No additional naming evidence has been recorded.</p>'
  if model.get('name_kind') == 'generated':
    naming = '<p>This label is generated from model type, architecture, variant, source date and bundle ID. It is not a published nickname.</p>'
  variants = []
  for v in model['variants']:
    recipe = catalog.resolve(v['recipe'])
    downloads = []
    for role, member in recipe.data['members'].items():
      location = catalog._data['locations'].get(member['artifact']['sha256'], {})
      for location_url in location.get('urls', []):
        url = urljoin(catalog.base_url or base_url, location_url)
        if urlparse(url).scheme in ('https', 'http') or (not urlparse(url).scheme and not urlparse(url).netloc and urlparse(url).path):
          downloads.append(f'<a href="{e(url, quote=True)}">Download {e(role.replace("_", " "))} weights</a>')
          break
    names = sorted({x['name'] for x in catalog._data['entries'] if x['recipe'] == v['recipe']})
    variants.append(f'''<section class="variant"><h3>{e(v['label'])}</h3>
      <p>{e(', '.join(v['formats']).upper())} · {v['size'] / 1024**2:.1f} MiB · recorded backend: {e(', '.join(v['targets']) or 'unspecified')}</p>
      <p>{' · '.join(downloads) or 'Weights are currently unavailable.'}</p>
      <p><a href="recipes/{v['recipe']}.json" download>Download configuration for SDK</a></p>
      <details><summary>Integration details and original archive name</summary><p>{e(', '.join(names))}</p>
      <p>Recipe ID: <code>{v['recipe']}</code></p><p>Profile: <a href="manifests/{recipe.profile.id}.json">{e(recipe.profile.data['name'])}</a></p>
      <p><a href="manifests/{v['recipe']}.json">View exact configuration and provenance</a></p></details></section>''')
  return shell(model['name'] + ' — openmodels', STYLE + f'''<main><a href="index.html">← All models</a>
    <section class="hero"><p>{e(model['publisher'])} · {e(model['kind'])} · {e(model['family'])}</p><h1>{e(model['name'])}</h1><p>{e(model['description'])}</p></section>
    {naming}<details><summary>Source references</summary><ul>{links}</ul></details><h2>Available configurations</h2><p>Each configuration preserves a specific source context. Your fork decides which configurations it supports and how to activate them. <a href="integrate.html">Integration guide</a></p>
    {''.join(variants)}</main>''')


def guide(shell):
  return shell('Build a model switcher — openmodels', STYLE + '''<main><section class="hero"><h1>Build a model switcher</h1><p>One static catalog, named models, and exact configurations. No hosted API required.</p></section>
  <ol><li>Load the catalog from this site or a pinned local snapshot.</li><li>Provide your fork’s support policy and show the resulting models and variants.</li><li>Install the selected recipe with progress and cancellation.</li><li>Prepare it with your runner, then activate it through your fork’s existing model manager.</li></ol>
  <pre><code>from openmodels import Catalog, ModelSwitcher

catalog = Catalog.load("https://jjolano.github.io/openmodels/catalog.json")
# approved_recipe_ids comes from your fork's tested model policy.
switcher = ModelSwitcher(catalog, "./model-store", support=lambda recipe:
    None if recipe.id in approved_recipe_ids else "Not supported by this fork")
models = switcher.list_models()
# Render model["name"] and its variants; retain variant["recipe"] as the selection.
package = switcher.install(selected_recipe_id,
    on_progress=lambda digest, received, total: update_progress(received, total),
    cancelled=lambda: cancel_requested)
# package.verify() has already passed. Activation belongs to your fork.</code></pre>
  <h2>Try the complete flow</h2><p>Clone the repository and run the self-contained demo. It serves synthetic weights locally, lists the model, installs it, and verifies its contents.</p>
  <pre><code>python examples/switcher.py --demo</code></pre>
  <p><a href="https://github.com/jjolano/openmodels/blob/main/docs/switcher.md">SDK installation, support policies and runner preparation</a> · <a href="https://github.com/jjolano/openmodels/blob/main/examples/switcher.py">Example switcher source</a></p>
  <h2>Other languages</h2><p><a href="models.json">models.json</a> provides display names and exact selectable recipe IDs. Load <a href="catalog.json">catalog.json</a> for their manifests, artifact locations and provenance. Verify every download against its declared size and SHA-256 before installation.</p>
  <p>Changing a selection never activates a model on a device. Keep your existing download worker, restart rules, rollback and driving-state checks in the consumer.</p></main>''')
