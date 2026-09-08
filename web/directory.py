"""Static composition-request workspace. Validation belongs to the Python SDK."""
from html import escape


def component_index(catalog):
  names = {}
  classes = {}
  hardware = {}
  for model in catalog.models(include_archive=True):
    for variant in model["variants"]:
      names.setdefault(variant["recipe"], (model["name"], model["publisher"], [n["name"] for n in model.get("names", [])]))
      classes[variant['recipe']] = variant.get('model_class', model.get('model_class', 'unknown'))
      hardware[variant['recipe']] = variant.get('hardware', [])
  profiles, components = [], []
  for digest, manifest in catalog._documents.items():
    data = manifest.data
    if data["type"] == "profile":
      profiles.append({"id": digest, "name": data["name"], "slot_sets": data["slot_sets"],
                       "required_configuration": data["required_configuration"], "connections": data["connections"]})
    elif data["type"] == "recipe":
      name, publisher, aliases = names.get(digest, ("Source configuration " + digest[:12], "", []))
      for role, member in data["members"].items():
        artifact, source = member["artifact"], member["source"]
        components.append({"recipe": digest, "role": role, "name": name, "publisher": publisher, "aliases": aliases,
          "model_class": classes.get(digest, 'unknown'), "targets": member['targets'],
          "hardware": hardware.get(digest, []),
          "context": str(source.get("commit") or source.get("revision") or "Unrecorded"),
          "format": artifact["format"], "sha256": artifact["sha256"], "size": artifact["size"],
          "available": catalog._data["locations"].get(artifact["sha256"], {}).get("availability") == "available"})
  profiles.sort(key=lambda p: (not any(set(s) == {"vision", "on_policy"} for s in p["slot_sets"]), p["name"]))
  components.sort(key=lambda c: (c["name"].casefold(), c["context"], c["recipe"], c["role"]))
  return {"revision": catalog.revision, "profiles": profiles, "components": components}


def render(catalog, shell, *, component_url="components.json", script_url="workbench.js"):
  return shell("Composition workbench — openmodels", f'''<main id="workbench" data-components="{escape(component_url, quote=True)}" data-revision="{catalog.revision}">
    <section class="workbench-heading"><div><h1>Composition workbench</h1><p>Assemble components. Inspect their sources. Export to your SDK.</p></div>
      <a class="revision" href="snapshots/{catalog.revision}.json" title="Download this exact catalog">Catalog <code>{catalog.revision[:12]}</code></a></section>
    <noscript><p class="notice">The workbench needs JavaScript. <a href="models.html">Browse models</a> or <a href="integrate.html">compose using the Python SDK</a>.</p></noscript>
    <div class="architecture-bar"><label for="execution-profile">Architecture<select id="execution-profile" disabled><option>Loading profiles…</option></select></label>
      <label for="slot-set">Components<select id="slot-set" disabled></select></label><button id="reset-workbench" type="button" disabled>Clear selections</button></div>
    <p id="compose-status" class="notice" role="status">Loading component library…</p><button id="retry-components" type="button" hidden>Retry loading library</button>
    <div class="workbench-layout">
      <section class="library panel" aria-labelledby="library-title"><div class="panel-heading"><h2 id="library-title">Component library</h2><span id="library-role" class="tag">Select a slot</span></div>
        <label class="search-label" for="component-search">Search components<input id="component-search" type="search" placeholder="Name, alias, source or hash" disabled></label>
        <div class="component-filters"><label>Model class<select id="component-class" disabled><option value="">All classes</option><option value="standard">Standard</option><option value="big">Big</option><option value="unknown">Unrecorded</option></select></label>
          <label>Execution target<select id="component-target" disabled><option value="">All targets</option></select></label>
          <button id="clear-component-filters" type="button" disabled>Clear filters</button></div>
        <p id="component-count" class="meta" role="status"></p><div id="component-list"></div>
        <div class="library-pages"><button id="component-prev" type="button" disabled>Previous</button><span id="component-page" class="meta"></span><button id="component-next" type="button" disabled>Next</button></div>
        <a class="library-browse" href="models.html">Browse the model directory</a></section>
      <section class="pipeline-panel panel" aria-labelledby="pipeline-title"><div class="panel-heading"><h2 id="pipeline-title">Model pipeline</h2><span id="slot-count" class="meta">No selection</span></div>
        <p class="panel-description">Select a slot, then choose its component from the library.</p><div id="pipeline"></div>
        <div class="pipeline-foot"><h3>Profile contract</h3><p id="profile-name" class="mono">Loading…</p><div id="profile-connections"></div><p class="meta">Connections describe the profile. The SDK checks your selected components.</p></div></section>
      <section class="inspector panel" aria-labelledby="inspector-title"><div class="panel-heading"><h2 id="inspector-title">Inspector</h2><span class="tag">Source facts</span></div>
        <div id="component-inspector"><p class="muted">Select a component to inspect its source context, tensors and recorded settings.</p></div>
        <div class="settings"><h3>Configuration overrides</h3><p class="meta">Only entered values become overrides. The SDK resolves source defaults.</p>
          <label for="recipe-settings">Overrides (JSON object)</label><textarea id="recipe-settings" rows="4" spellcheck="false" placeholder='{{"frame_skip": 4}}' disabled></textarea>
          <p id="settings-error" role="status"></p><h3>Required by this profile</h3><p id="required-settings" class="mono muted">Loading…</p></div></section>
    </div>
    <section class="export-panel panel" aria-labelledby="export-title"><div class="panel-heading"><div><h2 id="export-title">SDK handoff</h2><p class="meta">A composition request, not a validated recipe. Run the SDK to check it.</p></div>
      <a id="export-selection" class="primary-button" download="selection.json" hidden>Export selection.json</a></div>
      <div class="code-toolbar"><div class="code-tabs" aria-label="Export preview"><button type="button" data-code="python" aria-pressed="true">Python</button><button type="button" data-code="cli" aria-pressed="false">CLI</button><button type="button" data-code="request" aria-pressed="false">Request JSON</button></div><button id="copy-code" type="button" disabled>Copy Python</button></div>
      <pre id="python-code" tabindex="0">Choose a component for every slot to generate your SDK example.</pre><pre id="cli-code" tabindex="0" hidden></pre><pre id="request-code" tabindex="0" hidden></pre>
      <p id="copy-status" class="meta" role="status"></p><div class="handoff-notes"><span>SDK checks structure, settings and source pairing.</span><a href="integrate.html">Integrate with your model switcher</a></div></section>
    </main><script src="{escape(script_url, quote=True)}" defer></script>''')
