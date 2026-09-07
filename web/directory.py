"""Directory and composer using the shared catalog contracts."""
import html
import json

from openmodels.contracts import dumps


SCRIPT = r"""
(async function () {
  const status = document.getElementById('directory-status');
  const search = document.getElementById('directory-search');
  const rows = Array.from(document.querySelectorAll('[data-directory-entry]'));
  search.addEventListener('input', () => {
    const query = search.value.toLowerCase();
    let count = 0;
    rows.forEach(row => { row.hidden = !row.textContent.toLowerCase().includes(query); if (!row.hidden) count++; });
    status.textContent = count + ' recipes shown';
    document.getElementById('directory-empty').hidden = count !== 0;
  });
  const form = document.getElementById('recipe-composer');
  const profiles = document.getElementById('execution-profile');
  const slots = document.getElementById('recipe-slots');
  const message = document.getElementById('compose-status');
  const submit = document.getElementById('compose-recipe');
  const download = document.getElementById('download-recipe');
  const findings = document.getElementById('recipe-findings');
  let snapshot, documents, objectURL, request;
  function invalidate() {
    if (request || objectURL) message.textContent = 'Selections changed. Create a new recipe to check them.';
    if (request) { request.abort(); request = null; }
    if (objectURL) { URL.revokeObjectURL(objectURL); objectURL = null; }
    download.hidden = true;
    findings.replaceChildren();
    submit.disabled = !API_ENABLED;
  }
  function choose() {
    invalidate();
    slots.replaceChildren();
    const profile = documents[profiles.value];
    const group = document.createElement('select');
    group.id = 'slot-set';
    const groupLabel = document.createElement('label');
    groupLabel.htmlFor = group.id;
    groupLabel.textContent = 'Components';
    profile.slot_sets.forEach((roles, index) => group.add(new Option(roles.join(' + '), String(index))));
    slots.append(groupLabel, group);
    const choices = document.createElement('div');
    choices.className = 'recipe-fields';
    slots.append(choices);
    function buildSlots() {
      invalidate();
      choices.replaceChildren();
      profile.slot_sets[Number(group.value)].forEach((role, index) => {
        const label = document.createElement('label');
        label.textContent = role.replaceAll('_', ' ');
        const select = document.createElement('select');
        select.id = 'recipe-slot-' + index;
        select.dataset.role = role;
        select.required = true;
        label.htmlFor = select.id;
        select.add(new Option('Choose a component and source context', ''));
        snapshot.entries.forEach(entry => {
          const member = documents[entry.recipe].members[role];
          if (!member) return;
          const context = String(member.source.commit || member.source.revision || 'unspecified context').slice(0, 12);
          select.add(new Option(entry.name + ' · ' + context + ' · ' + member.artifact.sha256.slice(0, 8), entry.recipe));
        });
        choices.append(label, select);
      });
    }
    group.addEventListener('change', buildSlots);
    buildSlots();
    document.getElementById('required-settings').textContent = 'Required settings: ' + (profile.required_configuration.join(', ') || 'none');
    message.textContent = API_ENABLED ? 'Select components to create a recipe.' : 'This static mirror supports recipe downloads. Use the SDK or a self-hosted API to compose.';
  }
  try {
    const response = await fetch('snapshots/' + CATALOG_REVISION + '.json');
    if (!response.ok) throw new Error('Catalog unavailable');
    snapshot = await response.json();
    documents = Object.fromEntries(Object.entries(snapshot.documents).map(([id, raw]) => [id, JSON.parse(raw)]));
    Object.entries(documents).filter(([, doc]) => doc.type === 'profile').forEach(([id, doc]) => profiles.add(new Option(doc.name, id)));
    profiles.disabled = false;
    profiles.addEventListener('change', choose);
    form.addEventListener('input', invalidate);
    if (profiles.options.length) choose();
    else message.textContent = 'No execution profiles have been published yet.';
  } catch (error) {
    message.textContent = 'Could not load the composer. Recipe downloads below remain available. Reload to retry.';
    return;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!API_ENABLED) return;
    invalidate();
    const current = new AbortController();
    request = current;
    const timer = setTimeout(() => current.abort(), 30000);
    submit.disabled = true;
    message.textContent = 'Checking the selected components…';
    try {
      const selection = Object.fromEntries(Array.from(slots.querySelectorAll('[data-role]')).map(select =>
        [select.dataset.role, { recipe: select.value, slot: select.dataset.role }]));
      const text = document.getElementById('recipe-settings').value.trim();
      const body = { profile: profiles.value, selection };
      if (text) body.configuration = JSON.parse(text);
      const response = await fetch(API_BASE + '/v1/compose', { method: 'POST', headers: { 'Content-Type': 'application/json', 'If-Match': '"' + CATALOG_REVISION + '"' },
        body: JSON.stringify(body), signal: current.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check the selected components and settings.');
      if (request !== current) return;
      result.report.findings.forEach(finding => {
        const item = document.createElement('li');
        item.textContent = finding.code === 'configuration_unresolved' ? 'Choose a value for ' + finding.key + ' before preparation.' :
          finding.code === 'semantics_unverified' ? 'Component meanings have not been verified together.' :
          finding.code === 'structure_unknown' ? 'Some connection dimensions are unknown.' :
          finding.code === 'cross_lineage_or_unknown' ? 'This pairing has not been recorded upstream.' : finding.code.replaceAll('_', ' ');
        findings.append(item);
      });
      objectURL = URL.createObjectURL(new Blob([JSON.stringify(result.snapshot)], { type: 'application/json' }));
      download.href = objectURL;
      download.download = result.id + '.json';
      download.hidden = false;
      message.textContent = 'Recipe created. Execution support and qualification remain separate checks.';
    } catch (error) {
      if (request === current) message.textContent = error.name === 'AbortError' ? 'The request timed out. Try again.' : error.message;
    } finally {
      clearTimeout(timer);
      if (request === current) { request = null; submit.disabled = false; }
    }
  });
})();
"""


def render(catalog, shell, *, api_base="", api_enabled=False):
  entries = catalog.data["entries"]
  rows = []
  for entry in entries:
    recipe = catalog.resolve(entry["recipe"])
    contexts = sorted({str(m["source"].get("commit") or m["source"].get("revision") or "unrecorded")
                       for m in recipe.data["members"].values()})
    statuses = sorted({str(o.get("status", "unknown")) for o in entry["occurrences"]})
    rows.append(f'''<li data-directory-entry>
      <details><summary><strong>{html.escape(entry['name'])}</strong>
        <span class="meta">{html.escape(entry['publisher'])} · {html.escape(entry['kind'])}
        · context {html.escape(', '.join(c[:12] for c in contexts))}</span></summary>
        <p>Profile: <a href="manifests/{recipe.profile.id}.json">{html.escape(recipe.profile.data['name'])}</a></p>
        <p>History: {html.escape(', '.join(statuses) or 'publisher submission')}</p>
        <p>Recipe <code>{recipe.id}</code></p>
        <p><a href="recipes/{recipe.id}.json" download>Download recipe and dependencies</a>
        · <a href="manifests/{recipe.id}.json">View manifest</a></p>
      </details></li>''')
  body = f'''<h1 class="title">Advanced composer</h1>
    <p class="meta">Catalog updated {html.escape(catalog.data["generated_at"])} · {sum(v["availability"] == "available" for v in catalog.data["locations"].values())} of {len(catalog.data["locations"])} artifacts available</p>
    <p>Find model packages from comma and other publishers. Each recipe preserves its selected components and source configuration.</p>
    <details id="compose" class="universal-composer" open><summary>Compose a recipe</summary>
      <form id="recipe-composer" class="recipe-fields">
        <label for="execution-profile">Execution profile</label><select id="execution-profile" disabled></select>
        <fieldset id="recipe-slots"><legend>Model components</legend></fieldset>
        <label for="recipe-settings">Host settings (JSON, optional overrides)</label>
        <textarea id="recipe-settings" rows="3" aria-describedby="required-settings" placeholder='{{"frame_skip":4}}'></textarea>
        <p id="required-settings" class="meta"></p>
        <button id="compose-recipe" disabled>Create recipe</button>
        <p id="compose-status" role="status">Loading composer…</p>
        <ul id="recipe-findings"></ul>
        <a id="download-recipe" hidden>Download composed recipe</a>
      </form>
      <noscript>Composition uses JavaScript. You can also compose with the Python SDK.</noscript>
    </details>
    <div class="recipe-fields"><label for="directory-search">Search names, publishers, profiles, or source contexts</label>
      <input id="directory-search" type="search" placeholder="Search the directory"></div>
    <p id="directory-status" role="status">{len(entries)} recipes shown</p>
    <p id="directory-empty" hidden>No recipes match. Clear or change your search.</p>
    <ul class="directory-list">{''.join(rows)}</ul>
    <p><a href="catalog.json" download>Download the complete catalog</a> · <a href="schemas/recipe.json">Recipe schema</a>
    · <a href="https://github.com/jjolano/openmodels/blob/main/docs/universal.md">SDK and runner integration guide</a></p>
    <script>const CATALOG_REVISION={json.dumps(catalog.revision)}; const API_BASE={json.dumps(api_base).replace('<', chr(92) + 'u003c')}; const API_ENABLED={str(api_enabled).lower()};{SCRIPT}</script>'''
  return shell("Model directory — openmodels", body)
