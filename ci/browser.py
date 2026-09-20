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
    env = dict(os.environ)
    (root / 'index.json').write_text(json.dumps({'schema': 1, 'generated_at': '2026-09-07T00:00:00Z',
                                                'upstream_head': 'fixture', 'files': [], 'bundles': []}))
    subprocess.run([sys.executable, '-m', 'ci.site', 'build', '--index', str(root / 'index.json'),
                    '--out', str(root / 'openmodels'), '--code', 'fixture', '--archive', 'fixture'], check=True, env=env)
    # Use synthetic comma models for pagination; the pinned recipe remains an SDK-only fixture.
    from copy import deepcopy
    from types import SimpleNamespace
    from openmodels import Catalog
    from web.models import detail, discovery, filename, listing, page_filename
    from web.render import shell
    catalog = Catalog.load(root / 'openmodels/catalog.json')
    stock = {**catalog.models()[0], 'id': 'commaai/browser-model', 'name': 'Browse fixture',
             'publisher': 'commaai', 'archived': False, 'names': [], 'name_kind': 'published'}
    current = [stock] + [{**deepcopy(stock), 'id': f'example/page-{i}', 'name': f'Pagination model {i}',
                          'names': [], 'name_kind': 'generated', 'archived': False} for i in range(1, 61)]
    current[-1]['names'] = [{'name': 'Distant alias'}]
    for i, model in enumerate(current[1:], 1):
      model['model_class'] = 'standard' if i <= 30 else 'big' if i < 60 else 'unknown'
      for variant in model['variants']:
        variant['targets'] = ['QCOM' if i <= 30 else 'AMD']
    archived = [{**deepcopy(model), 'id': f'example/archive-{i}', 'name': f'Historical model {i}',
                 'names': [], 'name_kind': 'generated', 'archived': True}
                for i, model in enumerate(current)]
    models = current + archived
    fixture_catalog = SimpleNamespace(models=lambda **kwargs: [model for model in models
                                                               if kwargs.get('publisher') in (None, model['publisher'])])
    records = discovery(fixture_catalog)
    (root / 'openmodels' / filename(stock)).write_text(detail(catalog, stock, shell))
    (root / 'openmodels/discovery-test.json').write_text(json.dumps(records))
    for archive in (False, True):
      for page in (1, 2, 3):
        (root / 'openmodels' / page_filename(page, archive)).write_text(listing(
          fixture_catalog, shell, archive=archive, page=page, records=records, discovery_url='discovery-test.json'))
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
      browser('open', page_origin + '/openmodels/index.html')
      browser('snapshot', '-i')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30 && document.querySelector("#model-count").textContent.includes("of 61")')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("index-2.html") && document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'history.back()')
      browser('wait', '--fn', '!location.pathname.endsWith("index-2.html") && !document.querySelector(".model-card h2").textContent.includes("Pagination model")')
      browser('fill', '#model-search', 'Distant alias')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && document.querySelector(".model-card h2").textContent.includes("Pagination model 60")')
      browser('eval', 'if (new URLSearchParams(location.search).get("q") !== "Distant alias") throw Error("Search URL not preserved")')
      browser('reload')
      browser('wait', '--fn', 'document.querySelector("#model-search").value === "Distant alias" && document.querySelectorAll("[data-model]").length === 1')
      browser('fill', '#model-search', '')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("index-2.html")')
      browser('eval', 'if (!document.activeElement.matches("#model-pages [aria-current]")) throw Error("Pagination lost keyboard focus")')
      browser('eval', 'history.back()')
      browser('wait', '--fn', 'location.pathname.endsWith("index.html") && !document.querySelector(".model-card h2").textContent.includes("Pagination model")')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("index-2.html")')
      browser('select', '#model-naming', 'published')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && location.pathname.endsWith("index.html")')
      browser('eval', 'if (Number(new URLSearchParams(location.search).get("page") || 1) !== 1) throw Error("Filter did not reset page")')
      browser('select', '#model-naming', '')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("index-2.html")')
      browser('select', '#model-class', 'big')
      browser('wait', '--fn', 'location.pathname.endsWith("index.html") && document.querySelectorAll("[data-model]").length === 29')
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
      browser('open', page_origin + '/openmodels/index.html?page=999')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && document.querySelector(".model-card h2").textContent.includes("Pagination model 60")')
      browser('open', page_origin + '/openmodels/index.html?page=nonsense')
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
      browser('open', page_origin + '/openmodels/archive.html')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30 && document.querySelector("#model-count").textContent.includes("of 61")')
      browser('eval', 'if (!document.querySelector(".model-card h2").textContent.includes("Historical model")) throw Error("Archive contains current models")')
      browser('open', page_origin + '/openmodels/index.html')
      browser('set', 'viewport', '390', '844')
      browser('eval', "if (document.documentElement.scrollWidth > innerWidth) throw Error('Model mobile overflow')")
      browser('eval', 'document.querySelector(".model-card h2 a").scrollIntoView({block:"center"})')
      activate('.model-card:first-child h2 a')
      browser('wait', '.variant')
      recipe = catalog.resolve(catalog.data['entries'][0]['recipe'])
      browser('wait', '--fn', 'document.querySelector(\'a[href="recipes/' + recipe.id + '.json"]\') !== null')
      browser('eval', 'if (document.documentElement.scrollWidth > innerWidth) throw Error("Model page mobile overflow")')
      browser('eval', 'document.querySelector("#theme-toggle").click()')
      theme = json.loads(browser('eval', 'document.documentElement.dataset.theme'))
      browser('reload')
      assert json.loads(browser('eval', 'document.documentElement.dataset.theme')) == theme, 'Theme choice was not persisted'
      browser('eval', 'document.querySelector("#theme-toggle").click()')
      browser('open', page_origin + '/openmodels/')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('eval', 'if (document.documentElement.scrollWidth > innerWidth) throw Error("Directory mobile overflow")')
      print('Static directory browsing, search, pagination, model pages, theme persistence, filters, mobile layout and project Pages paths passed')
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
