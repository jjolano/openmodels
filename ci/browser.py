"""Exercise static Pages browsing and SDK composition exports in Chromium."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def main():
  with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    with socket.socket() as sock:
      sock.bind(('127.0.0.1', 0))
      port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    page_origin = origin
    env = {**os.environ, 'OPENMODELS_DATA': tmp, 'OPENMODELS_API_BASE': ''}
    (root / 'index.json').write_text(json.dumps({'schema': 1, 'generated_at': '2026-09-07T00:00:00Z',
                                                'upstream_head': 'fixture', 'files': [], 'bundles': []}))
    subprocess.run([sys.executable, '-m', 'ci.site', 'build', '--index', str(root / 'index.json'),
                    '--out', str(root / 'openmodels'), '--code', 'fixture', '--archive', 'fixture'], check=True, env=env)
    # Keep the real publisher's detail page and recipe; add enough discovery
    # records to exercise every pagination boundary without synthetic manifests.
    from copy import deepcopy
    from types import SimpleNamespace
    from openmodels import Catalog
    from web.models import discovery, listing, page_filename
    from web.render import shell
    catalog = Catalog.load(root / 'openmodels/catalog.json')
    stock = catalog.models()[0]
    models = [stock] + [{**deepcopy(stock), 'id': f'example/page-{i}', 'name': f'Pagination model {i}',
                         'names': [], 'name_kind': 'generated'} for i in range(1, 61)]
    models[-1]['names'] = [{'name': 'Distant alias'}]
    for i, model in enumerate(models[1:], 1):
      model['model_class'] = 'standard' if i <= 30 else 'big' if i < 60 else 'unknown'
      for variant in model['variants']:
        variant['targets'] = ['QCOM' if i <= 30 else 'AMD']
    fixture_catalog = SimpleNamespace(models=lambda **kwargs: models)
    records = discovery(fixture_catalog)
    (root / 'openmodels/discovery-test.json').write_text(json.dumps(records))
    for page in (1, 2, 3):
      (root / 'openmodels' / page_filename(page)).write_text(listing(
        fixture_catalog, shell, page=page, records=records, discovery_url='discovery-test.json'))
    server = subprocess.Popen([sys.executable, '-c', '''
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import sys
class Handler(SimpleHTTPRequestHandler):
  def end_headers(self):
    self.send_header('Cache-Control', 'no-store')
    super().end_headers()
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), partial(Handler, directory=sys.argv[2])).serve_forever()
''', str(port), tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    session = f'openmodels-ci-{os.getpid()}'
    def browser(*args):
      return subprocess.check_output(['agent-browser', '--session', session, *args], text=True, timeout=45)
    def activate(selector):
      browser('focus', selector)
      browser('press', 'Enter')
    try:
      for attempt in range(100):
        try:
          with urlopen(origin + '/openmodels/catalog.json', timeout=1):
            break
        except OSError:
          if server.poll() is not None or attempt == 99:
            raise
          time.sleep(0.1)
      browser('open', page_origin + '/openmodels/models.html')
      browser('snapshot', '-i')
      browser('eval', "if (document.querySelectorAll('[data-model]').length !== 30) throw Error('Initial page is not bounded')")
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("models-2.html") && document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'history.back()')
      browser('wait', '--fn', '!location.pathname.endsWith("models-2.html") && !document.querySelector(".model-card h2").textContent.includes("Pagination model")')
      browser('fill', '#model-search', 'Distant alias')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && document.querySelector(".model-card h2").textContent.includes("Pagination model 60")')
      browser('eval', 'if (new URLSearchParams(location.search).get("q") !== "Distant alias") throw Error("Search URL not preserved")')
      browser('reload')
      browser('wait', '--fn', 'document.querySelector("#model-search").value === "Distant alias" && document.querySelectorAll("[data-model]").length === 1')
      browser('fill', '#model-search', '')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("models-2.html")')
      browser('eval', 'if (!document.activeElement.matches("#model-pages [aria-current]")) throw Error("Pagination lost keyboard focus")')
      browser('eval', 'history.back()')
      browser('wait', '--fn', 'location.pathname.endsWith("models.html") && !document.querySelector(".model-card h2").textContent.includes("Pagination model")')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("models-2.html")')
      browser('select', '#model-naming', 'published')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && location.pathname.endsWith("models.html")')
      browser('eval', 'if (Number(new URLSearchParams(location.search).get("page") || 1) !== 1) throw Error("Filter did not reset page")')
      browser('select', '#model-naming', '')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("models-2.html")')
      browser('select', '#model-class', 'big')
      browser('wait', '--fn', 'location.pathname.endsWith("models.html") && document.querySelectorAll("[data-model]").length === 29')
      browser('select', '#model-target', 'AMD')
      browser('wait', '--fn', 'new URLSearchParams(location.search).get("target") === "AMD"')
      browser('reload')
      browser('wait', '--fn', 'document.querySelector("#model-class").value === "big" && document.querySelector("#model-target").value === "AMD" && document.querySelectorAll("[data-model]").length === 29')
      browser('select', '#model-target', 'QCOM')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 0')
      browser('select', '#model-class', 'unknown')
      browser('select', '#model-target', 'AMD')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && document.querySelector(".model-card h2").textContent.includes("Pagination model 60")')
      browser('select', '#model-class', '')
      browser('select', '#model-target', '')
      browser('wait', '--fn', '!location.search && document.querySelectorAll("[data-model]").length === 30')
      browser('open', page_origin + '/openmodels/models.html?page=999')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && document.querySelector(".model-card h2").textContent.includes("Pagination model 60")')
      browser('open', page_origin + '/openmodels/models.html?page=nonsense')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('fill', '#model-search', 'no such model')
      browser('wait', '--fn', '!document.querySelector("#model-empty").hidden && document.querySelectorAll("[data-model]").length === 0')
      browser('fill', '#model-search', '')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      (root / 'openmodels/discovery-test.json').rename(root / 'openmodels/discovery-unavailable.json')
      browser('reload')
      browser('fill', '#model-search', 'Distant alias')
      browser('wait', '--fn', 'document.querySelector("#model-load-status").textContent.includes("Search is unavailable.")')
      browser('eval', 'if (document.querySelectorAll("[data-model]").length !== 30 || !document.querySelector("#model-pages a[href]")) throw Error("Failed discovery removed static browsing")')
      (root / 'openmodels/discovery-unavailable.json').rename(root / 'openmodels/discovery-test.json')
      browser('open', page_origin + '/openmodels/models.html')
      browser('set', 'viewport', '390', '844')
      browser('eval', "if (document.documentElement.scrollWidth > innerWidth) throw Error('Model mobile overflow')")
      browser('eval', 'document.querySelector(".model-card h2 a").scrollIntoView({block:"center"})')
      activate('.model-card:first-child h2 a')
      browser('wait', '.variant')
      recipe = catalog.resolve(catalog.data['entries'][0]['recipe'])
      browser('open', page_origin + '/openmodels/?recipe=' + recipe.id)
      browser('wait', '--fn', '!document.querySelector("#export-selection").hidden')
      request = json.loads(browser('eval', 'JSON.parse(document.querySelector("#request-code").textContent)'))
      expected_selection = {role: {'recipe': recipe.id, 'slot': role} for role in recipe.data['members']}
      assert request['selection'] == expected_selection, request
      assert request['configuration'] == recipe.data['configuration'], request
      composed, _ = catalog.compose(**request)
      assert composed.data['members'] == recipe.data['members']
      assert composed.data['configuration'] == recipe.data['configuration']
      browser('eval', "if (document.documentElement.scrollWidth > innerWidth) throw Error('Workbench mobile overflow')")
      browser('eval', 'if (!document.querySelector("#python-code").textContent.includes(' + json.dumps(catalog.revision) + ')) throw Error("Python snapshot is not pinned")')
      browser('eval', 'if (!document.querySelector("#cli-code").textContent.includes(' + json.dumps(catalog.revision) + ')) throw Error("CLI snapshot is not pinned")')
      download = json.loads(browser('eval', '(async () => await (await fetch(document.querySelector("#export-selection").href)).json())()'))
      assert download == request
      browser('eval', 'window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: true}))')
      restored = json.loads(browser('eval', '(async () => await (await fetch(document.querySelector("#export-selection").href)).json())()'))
      assert restored == request, 'Back-forward cache lost the composition download'
      activate('#copy-code')
      browser('wait', '--fn', 'document.querySelector("#copy-status").textContent.includes("Copied")')
      example = json.loads(browser('eval', 'document.querySelector("#python-code").textContent'))
      subprocess.run([sys.executable, '-c', example], cwd=root, check=True,
        env={**env, 'PYTHONPATH': str(Path.cwd())}, capture_output=True, text=True)
      assert Catalog.load(root / 'recipe.json').resolve(composed.id) == composed
      browser('fill', '#recipe-settings', '{')
      browser('wait', '--fn', 'document.querySelector("#export-selection").hidden')
      browser('eval', 'if (document.querySelector("#request-code").textContent.includes(' + json.dumps(recipe.id) + ')) throw Error("Stale request remained visible")')
      browser('fill', '#recipe-settings', '{"frame_skip":8}')
      browser('wait', '--fn', '!document.querySelector("#export-selection").hidden')
      overridden = json.loads(browser('eval', 'JSON.parse(document.querySelector("#request-code").textContent)'))
      changed, _ = catalog.compose(**overridden)
      assert changed.data['configuration']['frame_skip'] == 8
      assert changed.data['members'] == recipe.data['members']
      browser('open', page_origin + '/openmodels/')
      browser('wait', '--fn', 'document.querySelector("#pipeline [data-slot]") !== null')
      browser('eval', 'if (!document.querySelector("#export-selection").hidden) throw Error("Fresh draft retained previous export")')
      browser('fill', '#component-search', 'no such component')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 0')
      browser('fill', '#component-search', '')
      browser('wait', '--fn', 'document.querySelector("#component-list [data-component]") !== null')
      browser('eval', 'if (document.querySelectorAll("#component-list [data-component]").length > 30) throw Error("Component DOM is unbounded")')
      activate('#component-list [data-component]:first-child')
      browser('wait', '--fn', '!document.querySelector("#export-selection").hidden')
      browser('eval', 'if (!document.activeElement.matches("#component-list [data-component]")) throw Error("Component selection lost keyboard focus")')
      activate('#pipeline [data-slot]')
      browser('eval', 'if (!document.activeElement.matches("#pipeline [data-slot]")) throw Error("Pipeline selection lost keyboard focus")')
      browser('eval', 'document.querySelector("#theme-toggle").click()')
      theme = json.loads(browser('eval', 'document.documentElement.dataset.theme'))
      browser('reload')
      assert json.loads(browser('eval', 'document.documentElement.dataset.theme')) == theme
      component_files = list((root / 'openmodels').glob('components-*.json'))
      assert len(component_files) == 1, component_files
      component_file = component_files[0]
      unavailable = component_file.with_suffix('.unavailable')
      component_file.rename(unavailable)
      browser('reload')
      browser('wait', '--fn', '!document.querySelector("#retry-components").hidden')
      browser('eval', 'if (!document.querySelector("#export-selection").hidden) throw Error("Failed load retained export")')
      unavailable.rename(component_file)
      activate('#retry-components')
      browser('wait', '--fn', 'document.querySelector("#component-list [data-component]") !== null')
      # Exercise library limits independently of the size of the publisher fixture.
      library = json.loads(component_file.read_text())
      item = library['components'][0]
      library['components'] = [{**item, 'name': f'Component page {i}',
        'model_class': 'standard' if i <= 30 else 'big' if i < 60 else 'unknown',
        'targets': ['QCOM' if i <= 30 else 'AMD']} for i in range(61)]
      component_file.write_text(json.dumps(library))
      browser('reload')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 30')
      first_page = browser('eval', 'document.querySelector("#component-list").textContent')
      activate('#component-next')
      browser('eval', 'if (document.querySelectorAll("#component-list [data-component]").length !== 30) throw Error("Second component page is not bounded")')
      assert browser('eval', 'document.querySelector("#component-list").textContent') != first_page
      activate('#component-next')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 1')
      browser('fill', '#component-search', 'Component page 5')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 11')
      browser('fill', '#component-search', 'Component page 60')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 1 && document.querySelector("#component-list").textContent.includes("Component page 60")')
      browser('fill', '#component-search', '')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 30')
      activate('#component-list [data-component]:first-child')
      browser('wait', '--fn', '!document.querySelector("#export-selection").hidden')
      selected = browser('eval', 'document.querySelector("#request-code").textContent')
      activate('#component-next')
      browser('select', '#component-class', 'standard')
      browser('wait', '--fn', 'document.querySelector("#component-prev").disabled && document.querySelectorAll("#component-list [data-component]").length === 30')
      browser('select', '#component-target', 'AMD')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 0')
      assert browser('eval', 'document.querySelector("#request-code").textContent') == selected, 'Filtering changed selected components'
      browser('select', '#component-class', 'unknown')
      browser('wait', '--fn', 'document.querySelectorAll("#component-list [data-component]").length === 1 && document.querySelector("#component-list").textContent.includes("Component page 60")')
      activate('#clear-component-filters')
      browser('wait', '--fn', 'document.querySelector("#component-class").value === "" && document.querySelector("#component-target").value === "" && document.querySelectorAll("#component-list [data-component]").length === 30')
      assert browser('eval', 'document.querySelector("#request-code").textContent') == selected, 'Clearing filters changed selected components'
      print('Static workbench export, SDK parity, overrides, failures, mobile layout, class/target filters, pagination and project Pages paths passed')
    except Exception:
      print(browser('get', 'url'))
      print(browser('snapshot', '-i'))
      raise
    finally:
      try:
        browser('close')
      finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
  main()
