/* Browser builds requests only. The shared Python SDK owns composition validation. */
(() => {
  'use strict';
  const root = document.getElementById('workbench');
  const $ = id => document.getElementById(id);
  const profileSelect = $('execution-profile'), group = $('slot-set'), search = $('component-search'), settings = $('recipe-settings');
  let index, profile, roles = [], activeRole, selected = {}, page = 1, version = 0, pending = false, objectURL, tab = 'python';
  const recipes = new Map(), size = 30, revision = root.dataset.revision;
  const catalogURL = new URL('snapshots/' + revision + '.json', location.href).href;
  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function message(text) { $('compose-status').textContent = text; }
  function label(role) { return ({vision: 'Vision encoder', on_policy: 'On-policy', off_policy: 'Off-policy', supercombo: 'Supercombo', dmonitoring: 'Driver monitoring', nav: 'Navigation'})[role] || role.replaceAll('_', ' '); }
  function ordered(slots) { const rank = {vision: 0, on_policy: 1, off_policy: 2}; return [...new Set(slots)].sort((a, b) => (rank[a] ?? 3) - (rank[b] ?? 3)); }
  function profileLabel(p) {
    const slots = ordered(p.slot_sets[0]);
    return slots.map(label).join(' + ') + (p.name.includes('555f48c5') ? ' · pinned stock' : '');
  }
  function invalidate() {
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = undefined;
    $('export-selection').hidden = true;
    $('export-selection').removeAttribute('href');
    $('copy-code').disabled = true;
    for (const name of ['python', 'cli', 'request']) $(name + '-code').textContent = 'Choose a component for every slot to generate your SDK example.';
    $('copy-status').textContent = '';
  }
  function request() {
    const text = settings.value.trim(), configuration = text ? JSON.parse(text) : undefined;
    if (configuration !== undefined && (configuration === null || Array.isArray(configuration) || typeof configuration !== 'object')) throw Error('Overrides must be a JSON object.');
    JSON.stringify(configuration, (_, value) => { if (typeof value === 'number' && !Number.isFinite(value)) throw Error('Overrides must contain finite numbers.'); return value; });
    const body = {profile: profile.id, selection: Object.fromEntries(roles.map(role => [role, {recipe: selected[role].record.recipe, slot: role}]))};
    if (configuration !== undefined) body.configuration = configuration;
    return body;
  }
  function exportRequest() {
    invalidate();
    $('settings-error').textContent = '';
    if (!profile || pending || !roles.length || roles.some(role => !selected[role])) return;
    let body;
    try { body = request(); } catch (error) { $('settings-error').textContent = 'Fix overrides: ' + error.message; message('Overrides need correction before export.'); return; }
    const raw = JSON.stringify(body, null, 2);
    $('request-code').textContent = raw;
    $('python-code').textContent = 'import json\nfrom pathlib import Path\nfrom openmodels import Catalog\n\n' +
      'catalog = Catalog.load(\n    ' + JSON.stringify(catalogURL) + ',\n    expected_sha256=' + JSON.stringify(revision) + ',\n)\n' +
      'request = json.loads(' + JSON.stringify(JSON.stringify(body)) + ')\n' +
      'recipe, report = catalog.compose(**request)\nprint(json.dumps(report, indent=2))\n' +
      'Path("recipe.json").write_text(catalog.export(recipe), encoding="utf-8")\n';
    const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
    $('cli-code').textContent = '# Save the exported selection.json, then run:\npython -m openmodels \\\n  --catalog ' + quote(catalogURL) + ' \\\n  --sha256 ' + revision + ' \\\n  compose selection.json > recipe.json\n';
    objectURL = URL.createObjectURL(new Blob([raw + '\n'], {type: 'application/json'}));
    $('export-selection').href = objectURL;
    $('export-selection').hidden = false;
    $('copy-code').disabled = false;
    message('Composition request prepared. Run the SDK to validate and create the recipe.');
  }
  async function getRecipe(id) {
    if (!/^[a-f0-9]{64}$/.test(id)) throw Error('Invalid source configuration ID.');
    if (!recipes.has(id)) recipes.set(id, (async () => {
      const response = await fetch('recipes/' + id + '.json');
      if (!response.ok) throw Error('Source configuration unavailable. Choose it again to retry.');
      const snapshot = await response.json(), raw = snapshot.documents?.[id];
      if (typeof raw !== 'string') throw Error('Source configuration is incomplete.');
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw))), b => b.toString(16).padStart(2, '0')).join('');
      if (hash !== id) throw Error('Source configuration failed its identity check.');
      return JSON.parse(raw);
    })().catch(error => { recipes.delete(id); throw error; }));
    return recipes.get(id);
  }
  function renderLibrary() {
    const focused = $('component-list').contains(document.activeElement) ? document.activeElement.dataset.component : undefined;
    const query = search.value.trim().toLowerCase();
    const found = index.components.filter(c => c.role === activeRole && c.search.includes(query));
    const pages = Math.max(1, Math.ceil(found.length / size)); page = Math.min(page, pages);
    $('library-role').textContent = label(activeRole || '');
    $('component-count').textContent = found.length ? `${(page - 1) * size + 1}–${Math.min(page * size, found.length)} of ${found.length} components` : 'No components match. Try another search or slot.';
    const buttons = found.slice((page - 1) * size, page * size).map(c => {
      const button = node('button', undefined, 'component-row'); button.type = 'button'; button.dataset.component = c.recipe; button.dataset.role = c.role;
      button.setAttribute('aria-pressed', String(selected[activeRole]?.record.recipe === c.recipe));
      button.append(node('strong', c.name), node('span', c.publisher + ' · ' + c.format.toUpperCase() + ' · ' + (c.size / 1048576).toFixed(1) + ' MiB', 'meta'), node('span', 'Source ' + c.context.slice(0, 12), 'mono meta'));
      button.addEventListener('click', () => chooseComponent(c)); return button;
    });
    $('component-list').replaceChildren(...buttons);
    if (focused) buttons.find(button => button.dataset.component === focused)?.focus({preventScroll: true});
    $('component-page').textContent = page + ' / ' + pages;
    $('component-prev').disabled = page === 1; $('component-next').disabled = page === pages;
  }
  function renderPipeline() {
    const focused = $('pipeline').contains(document.activeElement) ? document.activeElement.dataset.slot : undefined;
    $('slot-count').textContent = roles.filter(r => selected[r]).length + ' / ' + roles.length + ' selected';
    $('pipeline').replaceChildren(...roles.map(role => {
      const button = node('button', undefined, 'pipeline-slot'); button.type = 'button'; button.dataset.slot = role;
      button.setAttribute('aria-pressed', String(activeRole === role));
      button.append(node('span', label(role), 'slot-label'), node('strong', selected[role]?.record.name || 'Choose a component'), node('span', selected[role] ? 'Source ' + selected[role].record.context.slice(0, 12) : 'Select from the library', 'meta mono'));
      button.addEventListener('click', () => { activeRole = role; page = 1; search.value = ''; renderPipeline(); renderLibrary(); renderInspector(); if (matchMedia('(max-width:650px)').matches) $('library-title').scrollIntoView({block: 'start'}); $('pipeline').querySelector(`[data-slot="${CSS.escape(role)}"]`).focus({preventScroll: true}); });
      return button;
    }));
    if (focused) $('pipeline').querySelector(`[data-slot="${CSS.escape(focused)}"]`)?.focus({preventScroll: true});
  }
  function renderInspector() {
    const area = $('component-inspector'), choice = selected[activeRole];
    if (!choice) { area.replaceChildren(node('p', 'Choose a ' + label(activeRole || 'component').toLowerCase() + ' to inspect its source facts.', 'muted')); return; }
    const m = choice.recipe.members[activeRole], r = choice.record, facts = node('dl', undefined, 'facts');
    for (const [key, value] of [['Role', label(activeRole)], ['Format', r.format.toUpperCase()], ['Size', (r.size / 1048576).toFixed(1) + ' MiB'], ['Recorded target', (m.targets || []).join(', ') || 'Unrecorded'], ['Weights', r.available ? 'Download recorded' : 'Unavailable'], ['Source', r.context]]) {
      facts.append(node('dt', key), node('dd', value));
    }
    const link = node('a', 'Exact source configuration'); link.href = 'manifests/' + r.recipe + '.json';
    const sourceSettings = node('pre', JSON.stringify(m.configuration, null, 2));
    const details = node('details'), summary = node('summary', 'Tensors and metadata');
    details.append(summary, node('pre', JSON.stringify({inputs: m.inputs, outputs: m.outputs, metadata: m.metadata, missing: m.missing}, null, 2)));
    area.replaceChildren(node('h3', r.name), facts, link, node('h3', 'Source settings'), sourceSettings, details);
  }
  async function chooseComponent(record) {
    const current = ++version, role = activeRole;
    pending = true; delete selected[role]; invalidate(); renderPipeline(); renderInspector(); message('Loading source configuration…');
    try {
      const recipe = await getRecipe(record.recipe);
      if (current !== version) return;
      if (!recipe.members[role] || recipe.members[role].artifact.sha256 !== record.sha256) throw Error('Component index and source disagree. Reload the workbench.');
      selected[role] = {record, recipe}; pending = false;
      renderPipeline(); renderLibrary(); renderInspector(); exportRequest();
      if (roles.some(r => !selected[r])) message('Component selected. Choose a component for each remaining slot.');
    } catch (error) { if (current === version) { pending = false; message(error.message); renderLibrary(); } }
  }
  function selectSlots() {
    ++version; pending = false; selected = {}; activeRole = undefined;
    roles = ordered(profile.slot_sets[Number(group.value)]); activeRole = roles[0]; page = 1; search.value = ''; settings.value = '';
    invalidate(); $('settings-error').textContent = '';
    renderPipeline(); renderLibrary(); renderInspector(); message('Choose components. Their source contexts stay attached to your selection.');
  }
  function selectProfile() {
    profile = index.profiles.find(p => p.id === profileSelect.value);
    group.replaceChildren(...profile.slot_sets.map((s, i) => new Option(ordered(s).map(label).join(' + '), String(i))));
    group.closest('label').hidden = profile.slot_sets.length === 1;
    $('profile-name').textContent = profile.name;
    $('required-settings').textContent = profile.required_configuration.join(', ') || 'No required settings';
    $('profile-connections').replaceChildren(...profile.connections.map(c => node('p', c.from + '.' + c.output + ' → ' + c.to + '.' + c.input, 'connection mono')));
    selectSlots();
  }
  async function startFrom(id) {
    let current = ++version; pending = true; invalidate(); message('Loading starting configuration…');
    try {
      const recipe = await getRecipe(id);
      if (current !== version) return;
      const p = index.profiles.find(p => p.id === recipe.profile);
      if (!p) throw Error('Starting profile is absent from this catalog.');
      const wanted = Object.keys(recipe.members), slotSet = p.slot_sets.findIndex(s => { const unique = [...new Set(s)]; return unique.length === wanted.length && unique.every(r => wanted.includes(r)); });
      if (slotSet < 0) throw Error('Starting configuration has an unknown component layout.');
      profileSelect.value = p.id; selectProfile(); group.value = String(slotSet); selectSlots(); current = version;
      for (const role of roles) {
        const record = index.components.find(c => c.recipe === id && c.role === role);
        if (!record) throw Error('Starting component is absent from the catalog.');
        selected[role] = {record, recipe};
      }
      settings.value = JSON.stringify(recipe.configuration, null, 2);
      pending = false; renderPipeline(); renderLibrary(); renderInspector(); exportRequest();
    } catch (error) { if (current === version) { pending = false; invalidate(); message(error.message); } }
  }
  async function load() {
    $('retry-components').hidden = true;
    try {
      const response = await fetch(root.dataset.components);
      if (!response.ok) throw Error('Component library unavailable.');
      index = await response.json();
      if (index.revision !== revision || !index.profiles.length) throw Error('No matching profiles are available.');
      index.components.forEach(c => { c.search = [c.name, ...(c.aliases || []), c.publisher, c.context, c.sha256, c.format].join(' ').toLowerCase(); });
      profileSelect.replaceChildren(...index.profiles.map(p => new Option(profileLabel(p) + (index.profiles.filter(q => profileLabel(q) === profileLabel(p)).length > 1 ? ' · ' + p.id.slice(0, 6) : ''), p.id)));
      for (const el of [profileSelect, group, search, settings, $('reset-workbench')]) el.disabled = false;
      selectProfile();
      const starter = new URLSearchParams(location.search).get('recipe');
      if (starter) await startFrom(starter);
    } catch (error) { message(error.message + ' Retry or browse Models to download existing configurations.'); $('retry-components').hidden = false; }
  }
  profileSelect.addEventListener('change', selectProfile); group.addEventListener('change', selectSlots);
  $('reset-workbench').addEventListener('click', () => { history.replaceState(null, '', location.pathname); selectSlots(); });
  settings.addEventListener('input', exportRequest);
  search.addEventListener('input', () => { page = 1; renderLibrary(); });
  $('component-prev').addEventListener('click', () => { --page; renderLibrary(); });
  $('component-next').addEventListener('click', () => { ++page; renderLibrary(); });
  $('retry-components').addEventListener('click', load);
  document.querySelectorAll('[data-code]').forEach(button => button.addEventListener('click', () => {
    tab = button.dataset.code;
    document.querySelectorAll('[data-code]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    for (const name of ['python', 'cli', 'request']) $(name + '-code').hidden = name !== tab;
    $('copy-code').textContent = 'Copy ' + button.textContent;
  }));
  $('copy-code').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($(tab + '-code').textContent); $('copy-status').textContent = 'Copied to clipboard.'; }
    catch (_) { $('copy-status').textContent = 'Clipboard unavailable. Select and copy the preview text.'; $(tab + '-code').focus(); }
  });
  window.addEventListener('pagehide', event => { if (!event.persisted && objectURL) URL.revokeObjectURL(objectURL); });
  load();
})();
