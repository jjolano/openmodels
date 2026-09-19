"""Static model browsing and exact configuration downloads."""
from html import escape as e
from urllib.parse import urljoin, urlparse
from openmodels.contracts import sha256

NAME_KINDS = {"published": "Published name", "source": "Source-derived label", "generated": "Generated label"}


def filename(model):
  return 'model-' + sha256(model['id'].encode())[:24] + '.html'


STYLE = '''<style>
.hero{padding:0 0 20px;max-width:850px}.hero h1{font-size:clamp(26px,3vw,36px);line-height:1.15;letter-spacing:-.035em;margin:0 0 10px}.hero p{color:var(--muted)}
.model-grid{display:grid;margin-top:16px;border-top:1px solid var(--line)}.model-card{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,.65fr) auto;gap:14px;align-items:center;overflow-wrap:anywhere;padding:18px 4px;border-bottom:1px solid var(--line)}
.model-card h2{font-size:17px;line-height:1.35;margin:3px 0}.model-card h2 a{text-decoration:none;color:var(--ink)}.model-card h2 a:hover{color:var(--accent)}.model-card p{margin:4px 0;font-size:13px}.model-card .model-action{font-size:13px;white-space:nowrap;padding:12px 0}
.filters{display:flex;gap:16px;flex-wrap:wrap;align-items:end}.filters label{display:grid;gap:6px;flex:1;min-width:160px}.advanced-filters{margin:14px 0}.advanced-filters label{display:grid;gap:6px;max-width:300px;margin-top:12px}
input,select{font:inherit;padding:10px;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:5px}a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:4px}summary{cursor:pointer}
.variant{padding:20px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}.variant h3{margin:0 0 10px}.variant summary{padding:12px 0}.variant-actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.variant-actions a{display:inline-block;padding:10px 14px;border:1px solid var(--line);border-radius:5px;text-decoration:none}.variant-actions .start-model{color:var(--bg);background:var(--accent);border-color:var(--accent);font-weight:600}
.build{margin:12px 0;padding:14px;background:var(--panel);border:1px solid var(--line);border-radius:5px}.build h4{margin:0 0 8px;font-size:14px}.build p{margin:4px 0;font-size:13px}
pre{background:var(--code);padding:18px;overflow:auto}.provenance{margin-top:24px;border-top:1px solid var(--line);padding-top:20px}.provenance>summary{font-weight:600;padding:10px 0}
#model-pages{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}#model-pages a{min-width:44px;min-height:44px;padding:8px 12px;text-align:center;border:1px solid var(--line);border-radius:5px}#model-pages [aria-current]{background:var(--accent);color:var(--bg);font-weight:600}
@media(max-width:650px){.model-card{grid-template-columns:minmax(0,1fr) auto;gap:8px}.model-card .model-facts{grid-column:1}.model-card .model-action{grid-column:2;grid-row:1 / 3}.variant-actions{align-items:stretch}.variant-actions a{width:100%}}
</style>'''
SCRIPT = r"""<script>
(async () => {
  const grid = document.querySelector('.model-grid'), nav = document.querySelector('#model-pages');
  const search = document.querySelector('#model-search'), kind = document.querySelector('#model-kind'), naming = document.querySelector('#model-naming');
  const modelClass = document.querySelector('#model-class'), target = document.querySelector('#model-target');
  const status = document.querySelector('#model-load-status');
  const archive = grid.dataset.archive === 'true', size = 30;
  function pageFile(page) { return page === 1 ? (archive ? 'archive.html' : 'index.html') : (archive ? 'archive-' : 'index-') + page + '.html'; }
  function readState() {
    const params = new URLSearchParams(location.search);
    search.value = params.get('q') || '';
    kind.value = params.get('kind') || '';
    naming.value = params.get('naming') || '';
    modelClass.value = params.get('class') || '';
    target.value = params.get('target') || '';
    if (naming.value) naming.closest('details').open = true;
    const pathPage = location.pathname.match(/(?:index|archive)-(\d+)\.html$/);
    const value = params.get('page') || (pathPage ? pathPage[1] : '1');
    return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Math.max(1, Number(value)) : 1;
  }
  function pageURL(page) {
    const params = new URLSearchParams();
    if (search.value) params.set('q', search.value);
    if (kind.value) params.set('kind', kind.value);
    if (naming.value) params.set('naming', naming.value);
    if (modelClass.value) params.set('class', modelClass.value);
    if (target.value) params.set('target', target.value);
    if (page > 1) params.set('page', page);
    return pageFile(page) + (params.size ? '?' + params : '');
  }
  let records, loading, timer;
  function load() {
    if (!loading) loading = fetch(grid.dataset.discovery).then(response => {
      if (!response.ok) throw Error('Discovery unavailable');
      return response.json();
    }).then(data => { records = data.map(m => ({...m, search: [m.name, m.aliases, m.publisher, m.kind, m.family, m.formats, m.name_label, m.summary, m.model_class, ...m.targets, ...m.hardware].join(' ').toLowerCase()})); }).catch(error => { loading = null; throw error; });
    return loading;
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function card(model) {
    const node = element('article', undefined, 'model-card');
    node.dataset.model = '';
    const identity = element('div'), facts = element('div', undefined, 'model-facts');
    identity.append(element('div', model.publisher + ' · ' + model.kind, 'meta'));
    const heading = element('h2'), link = element('a', model.name);
    link.href = model.href; heading.append(link); identity.append(heading);
    identity.append(element('p', model.name_label, 'meta'));
    if (model.aliases) identity.append(element('p', 'Also listed as ' + model.aliases, 'meta'));
    facts.append(element('p', model.family + ' · ' + model.formats));
    const badges = element('p', undefined, 'model-badges');
    badges.append(element('span', 'Class: ' + model.class_label, 'fact-badge'), element('span', 'Target: ' + model.targets.map(t => t === 'unknown' ? 'Unrecorded' : t).join(', '), 'fact-badge'));
    for (const hardware of model.hardware) badges.append(element('span', hardware + ' source recorded', 'fact-badge'));
    facts.append(badges);
    facts.append(element('p', model.summary, 'meta'));
    const view = element('a', 'View model →', 'model-action'); view.href = model.href;
    node.append(identity, facts, view);
    return node;
  }
  function render(page, historyMode) {
    const query = search.value.toLowerCase();
    const found = records.filter(m => (archive ? m.archived : !m.archived) && (!kind.value || m.kind === kind.value) &&
      (!naming.value || m.name_kind === naming.value) && (!modelClass.value || m.model_class === modelClass.value) &&
      (!target.value || m.targets.includes(target.value)) && m.search.includes(query));
    const pages = Math.max(1, Math.ceil(found.length / size));
    page = Math.max(1, Math.min(page, pages));
    grid.replaceChildren(...found.slice((page - 1) * size, page * size).map(card));
    document.querySelector('#model-count').textContent = found.length ?
      'Showing ' + ((page - 1) * size + 1) + '–' + Math.min(page * size, found.length) + ' of ' + found.length + ' models' : '0 models';
    document.querySelector('#model-empty').hidden = found.length !== 0;
    nav.replaceChildren();
    for (let i = 1; i <= pages; i++) {
      const link = element('a', String(i)); link.href = pageURL(i); link.dataset.page = String(i);
      link.setAttribute('aria-label', 'Page ' + i);
      if (i === page) link.setAttribute('aria-current', 'page');
      nav.append(link);
    }
    const url = pageURL(page);
    const current = location.pathname.split('/').pop() + location.search;
    if (url !== current) history[historyMode + 'State'](null, '', url);
    status.textContent = '';
  }
  let request = 0;
  async function update(page, historyMode = 'push', focusPage = false) {
    const current = ++request;
    status.textContent = 'Searching the catalog…';
    try {
      await load();
      if (current === request) {
        render(page, historyMode);
        if (focusPage) nav.querySelector('[aria-current]').focus({preventScroll: true});
      }
    } catch (_) {
      if (current === request) status.textContent = 'Search is unavailable. Showing the last loaded results; use the page links to browse or reload to retry.';
    }
  }
  search.addEventListener('input', () => { clearTimeout(timer); ++request; timer = setTimeout(() => update(1), 200); });
  for (const control of [kind, naming, modelClass, target]) control.addEventListener('change', () => { clearTimeout(timer); update(1); });
  nav.addEventListener('click', event => {
    const link = event.target.closest('a[data-page]');
    if (!link || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || !records) return;
    event.preventDefault(); clearTimeout(timer); update(Number(link.dataset.page), 'push', true);
    document.querySelector('#model-count').scrollIntoView({block: 'start'});
  });
  window.addEventListener('popstate', () => { clearTimeout(timer); update(readState(), 'replace'); });
  for (const control of [search, kind, naming, modelClass, target]) control.disabled = false;
  const page = readState();
  if (location.search) update(page, 'replace');
})();
</script>
"""

PAGE_SIZE = 30
CLASS_LABELS = {'standard': 'Standard', 'big': 'Big', 'unknown': 'Unrecorded'}


def badges(model_class, targets):
  return f'<p class="model-badges"><span class="fact-badge">Class: {e(CLASS_LABELS[model_class])}</span><span class="fact-badge">Target: {e(", ".join("Unrecorded" if t == "unknown" else t for t in targets) or "Unrecorded")}</span></p>'


def page_filename(page, archive=False):
  return ('archive.html' if archive else 'index.html') if page == 1 else f"{'archive' if archive else 'index'}-{page}.html"


def discovery(catalog):
  records = []
  for model in catalog.models(include_archive=True):
    name_kind = model.get('name_kind', 'published')
    aliases = ', '.join(dict.fromkeys(n['name'] for n in model.get('names', []) if n['name'].casefold() != model['name'].casefold()))
    records.append({
      'model_class': model.get('model_class', 'unknown'), 'class_label': CLASS_LABELS[model.get('model_class', 'unknown')],
      'targets': sorted({t for v in model['variants'] for t in (v['targets'] or ['unknown'])}),
      'hardware': sorted({h['name'] for v in model['variants'] for h in v.get('hardware', [])}),
      'name': model['name'], 'href': filename(model), 'publisher': model['publisher'], 'kind': model['kind'],
      'family': model['family'], 'formats': ', '.join(sorted({f for v in model['variants'] for f in v['formats']})).upper(),
      'aliases': aliases, 'name_kind': name_kind, 'name_label': NAME_KINDS[name_kind], 'archived': model['archived'],
      'summary': f"{len(model['variants'])} source configurations · " + ('Downloads available' if any(v['available'] for v in model['variants']) else 'Downloads unavailable')})
  return records


def card(model):
  return f'''<article class="model-card" data-model>
    <div class="model-identity"><div class="meta">{e(model['publisher'])} · {e(model['kind'])}</div>
    <h2><a href="{model['href']}">{e(model['name'])}</a></h2>
    <p class="meta">{e(model['name_label'])}</p>
    {f'<p class="meta">Also listed as {e(model["aliases"])}</p>' if model['aliases'] else ''}
    </div><div class="model-facts"><p>{e(model['family'])} · {e(model['formats'])}</p>{badges(model['model_class'], model['targets'])}{''.join(f'<span class="fact-badge">{e(h)} source recorded</span>' for h in model['hardware'])}<p class="meta">{e(model['summary'])}</p></div>
    <a class="model-action" href="{model['href']}">View model →</a></article>'''


def listing(catalog, shell, *, archive=False, page=1, records=None, discovery_url='discovery.json'):
  records = discovery(catalog) if records is None else records
  models = [m for m in records if m['archived'] == archive]
  pages = max(1, (len(models) + PAGE_SIZE - 1) // PAGE_SIZE)
  page = max(1, min(page, pages))
  start = (page - 1) * PAGE_SIZE
  cards = ''.join(card(m) for m in models[start:start + PAGE_SIZE])
  links = ''.join(f'<a href="{page_filename(i, archive)}" data-page="{i}" aria-label="Page {i}"' +
                  (' aria-current="page"' if i == page else '') + f'>{i}</a>' for i in range(1, pages + 1))
  title = 'Historical models' if archive else 'Current models'
  intro = 'Models outside the current upstream tree, including published names and training runs.' if archive else 'Models currently present in the upstream tree, including published names and source configurations.'
  options = ''.join(f'<option value="{e(k, quote=True)}">{e(k.capitalize())}</option>' for k in sorted({m['kind'] for m in models}))
  targets = ''.join(f'<option value="{e(t, quote=True)}">{e(t)}</option>' for t in sorted({t for m in models for t in m['targets']} - {'unknown'}))
  count = f'Showing {start + 1}–{min(start + PAGE_SIZE, len(models))} of {len(models)} models' if models else '0 models'
  return shell(title + f' — page {page} — openmodels', STYLE + f'''<main><section class="hero"><h1>{title}</h1><p>{intro}</p></section>
    <div class="filters"><label for="model-search">Search models<input disabled type="search" id="model-search" placeholder="Search {'historical' if archive else 'current'} models by name or alias"></label>
    <label for="model-kind">Model type<select disabled id="model-kind"><option value="">All types</option>{options}</select></label>
    <label for="model-class">Model class<select disabled id="model-class"><option value="">All classes</option><option value="standard">Standard</option><option value="big">Big</option><option value="unknown">Unrecorded</option></select></label>
    <label for="model-target">Execution target<select disabled id="model-target"><option value="">All targets</option>{targets}<option value="unknown">Unrecorded</option></select></label>
    </div><details class="advanced-filters"><summary>Advanced filters</summary><label for="model-naming">Naming<select disabled id="model-naming"><option value="">All names and labels</option><option value="published">Published names</option><option value="source">Source-derived labels</option><option value="generated">Generated labels</option></select></label></details>
    <p class="meta">Class describes the upstream model variant; target is the recorded execution backend. Chestnut labels identify source evidence for specific configurations, not device qualification.</p>
    <noscript>Search and filters require JavaScript. Browse {'historical' if archive else 'current'} models using the page links below.</noscript>
    <p id="model-load-status" role="status"></p><p id="model-count" role="status">{count}</p>
    <p id="model-empty" {'' if not models else 'hidden'}>No models match. Clear your search or choose another type.</p>
    <div class="model-grid" data-archive="{str(archive).lower()}" data-discovery="{e(discovery_url, quote=True)}">{cards}</div>
    <nav id="model-pages" aria-label="Model pages">{links}</nav>
    <p>{'Browse <a href="index.html">current models</a>.' if archive else 'Browse <a href="archive.html">historical models</a>. Generated labels identify weights without claiming a published nickname.'}</p></main>''' + SCRIPT)


BUILD_NOTICE = ("Compiled off-device with qemu + LLVM. GPU parity, device execution and timing "
                "are unverified; no device validation is claimed.")


def precompiled(record):
  target, artifact = record['target'], record['artifact']
  checks = ' · '.join(e(check.replace('-', ' ')) for check in record['checks'])
  return f'''<section class="build"><h4>Precompiled a630 build (off-device)</h4>
      <p>Target <code>{e(target['backend'])} / {e(target['hardware'])}</code> · runtime <code>{e(target['runtime'])}</code></p>
      <p><a href="{e(artifact['url'], quote=True)}" download>{e(artifact['name'])}</a> · {artifact['size'] / 1024**2:.1f} MiB</p>
      <p>SHA-256 <code>{e(artifact['sha256'])}</code></p>
      <p class="meta">Checks: {checks}. {BUILD_NOTICE}</p></section>'''


def detail(catalog, model, shell, *, base_url="", builds=()):
  built = {record['recipe']: record for record in builds}
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
    hardware = ''.join(f'<p><a href="{e(h["url"], quote=True)}">{e(h["name"])} source evidence</a> for this configuration. Source usage does not establish device qualification.</p>' for h in v.get('hardware', []) if urlparse(h['url']).scheme in ('https', 'http'))
    variants.append(f'''<section class="variant"><h3>{e(v['label'])}</h3>
      {badges(v.get('model_class', model.get('model_class', 'unknown')), v['targets'])}
      {hardware}
      <p>{e(', '.join(v['formats']).upper())} · {v['size'] / 1024**2:.1f} MiB</p>
      <p>{' · '.join(downloads) or 'Weights are currently unavailable.'}</p>
      {precompiled(built[v['recipe']]) if v['recipe'] in built else ''}
      <div class="variant-actions"><a href="recipes/{v['recipe']}.json" download>Download configuration for SDK</a></div>
      <details><summary>Integration details and original archive name</summary><p>{e(', '.join(names))}</p>
      <p>Recipe ID: <code>{v['recipe']}</code></p><p>Profile: <a href="manifests/{recipe.profile.id}.json">{e(recipe.profile.data['name'])}</a></p>
      <p><a href="manifests/{v['recipe']}.json">View exact configuration and provenance</a></p></details></section>''')
  return shell(model['name'] + ' — openmodels', STYLE + f'''<main><a href="index.html">← All models</a>
    <section class="hero"><p>{e(model['publisher'])} · {e(model['kind'])} · {e(model['family'])}</p><h1>{e(model['name'])}</h1><p>{e(model['description'])}</p></section>
    <h2>Available configurations</h2><p>Each configuration preserves a specific source context. Your fork decides which configurations it supports and how to activate them. <a href="integrate.html">Integration guide</a></p>
    {''.join(variants)}<details class="provenance"><summary>Names, aliases and provenance</summary>{naming}<h3>Source references</h3><ul>{links}</ul></details></main>''')


def guide(shell):
  return shell('Developers — openmodels', STYLE + '''<main><section class="hero"><h1>Downloads and SDK</h1><p>One static catalog of comma models: browse it, pin a snapshot, and install verified weights with the dependency-free Python SDK.</p></section>
  <h2>Install a configuration</h2><p>Model pages list every recorded source configuration. Each one is a recipe: an immutable document binding an exact artifact digest, its source commit, and the settings that commit used. <a href="catalog.json">catalog.json</a> carries those documents verbatim, and <a href="models.json">models.json</a> offers the same discovery view to other languages.</p>
  <pre><code>from pathlib import Path

from openmodels import Catalog, ModelStore

catalog = Catalog.load("https://jjolano.github.io/openmodels/catalog.json")
recipe = catalog.resolve("Stock supercombo (555f48c5)")
package = ModelStore(Path("./models"), catalog).fetch(recipe)
package.verify()  # size and SHA-256 of every artifact, already checked before install</code></pre>
  <p>Pass the digest from a reviewed <code>deployment.json</code> record as <code>expected_sha256</code> to pin the exact snapshot. Downloads stage into a temporary directory, verify the declared size and SHA-256, and rename into place, so an interrupted download never becomes an installed package. Verified artifacts are cached by digest and hard-linked between recipes.</p>
  <pre><code>python -m openmodels --catalog https://jjolano.github.io/openmodels/catalog.json fetch "Stock supercombo (555f48c5)" --store ./models
python -m openmodels --catalog catalog.json list --query supercombo
python -m openmodels --catalog catalog.json export RECIPE_ID &gt; selected.json</code></pre>
  <h2>Precompiled a630 builds</h2><p>Some model configurations also ship a build compiled off-device for comma 3X (Qualcomm a630). Model pages show the artifact name, size, SHA-256 and download URL; <a href="builds.json">builds.json</a> lists every record with its compiler identity and target.</p>
  <p>These builds are evidence of compilation only. They were produced with <code>qemu</code> and LLVM on x86-64, and GPU parity, device execution and timing remain unverified: every record carries <code>gpu_validated: false</code> and <code>device_validated: false</code>. Verify the artifact digest against <code>builds.json</code> before use, and treat qualification as your own responsibility.</p>
  <h2>What the catalog guarantees</h2><p>It asserts blob identity and upstream provenance. Every artifact digest, source commit and recorded host constant comes from the archive import; blobs in Releases are append-only, and each publication is retained once as <code>catalog-DIGEST</code> so older snapshots stay usable after the site moves on.</p>
  <p>Changing a selection never activates a model on a device. Scheduling, camera buffers, calibration, output publication, qualification, activation and rollback stay in the consumer. Archived comma models remain under the upstream MIT license; see <a href="https://github.com/jjolano/openmodels/blob/main/THIRD_PARTY_NOTICES.md">third-party notices</a>.</p></main>''')
