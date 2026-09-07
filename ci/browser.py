"""Exercise the generated project Pages site and real API in Chromium."""
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
    page_origin = f'http://localhost:{port}'
    env = {**os.environ, 'OPENMODELS_DATA': tmp, 'OPENMODELS_API_BASE': origin,
           'OPENMODELS_CORS_ORIGINS': page_origin}
    (root / 'index.json').write_text(json.dumps({'schema': 1, 'generated_at': '2026-09-07T00:00:00Z',
                                                'upstream_head': 'fixture', 'files': [], 'bundles': []}))
    subprocess.run([sys.executable, '-m', 'ci.site', 'build', '--index', str(root / 'index.json'),
                    '--out', str(root / 'public'), '--code', 'fixture', '--archive', 'fixture'], check=True, env=env)
    # Keep the real publisher's detail page and API recipe; add enough discovery
    # records to exercise every pagination boundary without synthetic manifests.
    from copy import deepcopy
    from types import SimpleNamespace
    from openmodels import Catalog
    from web.models import discovery, listing, page_filename
    from web.render import shell
    catalog = Catalog.load(root / 'public/catalog.json')
    stock = catalog.models()[0]
    models = [stock] + [{**deepcopy(stock), 'id': f'example/page-{i}', 'name': f'Pagination model {i}',
                         'names': [], 'name_kind': 'generated'} for i in range(1, 61)]
    models[-1]['names'] = [{'name': 'Distant alias'}]
    fixture_catalog = SimpleNamespace(models=lambda **kwargs: models)
    records = discovery(fixture_catalog)
    (root / 'public/discovery-test.json').write_text(json.dumps(records))
    for page in (1, 2, 3):
      (root / 'public' / page_filename(page)).write_text(listing(
        fixture_catalog, shell, page=page, records=records, discovery_url='discovery-test.json'))
    server = subprocess.Popen([sys.executable, '-c', f'''
import os
from api.main import app
from fastapi.staticfiles import StaticFiles
import uvicorn
app.mount('/openmodels', StaticFiles(directory=os.environ['OPENMODELS_DATA'] + '/public', html=True))
uvicorn.run(app, host='127.0.0.1', port={port}, log_level='error')
'''], env=env)
    session = f'openmodels-ci-{os.getpid()}'
    def browser(*args):
      return subprocess.check_output(['agent-browser', '--session', session, *args], text=True, timeout=45)
    try:
      for attempt in range(100):
        try:
          with urlopen(origin + '/v1/status', timeout=1):
            break
        except OSError:
          if server.poll() is not None or attempt == 99:
            raise
          time.sleep(0.1)
      browser('open', page_origin + '/openmodels/')
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
      browser('wait', '--fn', 'location.pathname.endsWith("index.html") && !document.querySelector(".model-card h2").textContent.includes("Pagination model")')
      browser('eval', 'Array.from(document.querySelectorAll("#model-pages [data-page]")).find(a=>a.dataset.page==="2").click()')
      browser('wait', '--fn', 'location.pathname.endsWith("models-2.html")')
      browser('select', '#model-naming', 'published')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && location.pathname.endsWith("index.html")')
      browser('eval', 'if (Number(new URLSearchParams(location.search).get("page") || 1) !== 1) throw Error("Filter did not reset page")')
      browser('open', page_origin + '/openmodels/index.html?page=999')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 1 && document.querySelector(".model-card h2").textContent.includes("Pagination model 60")')
      browser('open', page_origin + '/openmodels/index.html?page=nonsense')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      browser('fill', '#model-search', 'no such model')
      browser('wait', '--fn', '!document.querySelector("#model-empty").hidden && document.querySelectorAll("[data-model]").length === 0')
      browser('fill', '#model-search', '')
      browser('wait', '--fn', 'document.querySelectorAll("[data-model]").length === 30')
      (root / 'public/discovery-test.json').rename(root / 'public/discovery-unavailable.json')
      browser('reload')
      browser('fill', '#model-search', 'Distant alias')
      browser('wait', '--fn', 'document.querySelector("#model-load-status").textContent.includes("Search is unavailable.")')
      browser('eval', 'if (document.querySelectorAll("[data-model]").length !== 30 || !document.querySelector("#model-pages a[href]")) throw Error("Failed discovery removed static browsing")')
      (root / 'public/discovery-unavailable.json').rename(root / 'public/discovery-test.json')
      browser('open', page_origin + '/openmodels/index.html')
      browser('set', 'viewport', '390', '844')
      browser('eval', "if (document.documentElement.scrollWidth > innerWidth) throw Error('Model mobile overflow')")
      browser('eval', 'document.querySelector(".model-card h2 a").scrollIntoView({block:"center"})')
      browser('click', '.model-card:first-child h2 a')
      browser('wait', '.variant')
      browser('eval', 'document.querySelector("nav a:nth-child(4)").scrollIntoView({block:"center"})')
      browser('click', 'a[href="compose.html"]')
      browser('wait', '#recipe-slot-0')
      recipe_id = catalog.data['entries'][0]['recipe']
      browser('select', '#recipe-slot-0', recipe_id)
      browser('eval', 'document.querySelector("#compose-recipe").scrollIntoView({block:"center"})')
      browser('click', '#compose-recipe')
      browser('wait', '--fn', '!document.querySelector("#download-recipe").hidden')
      result = browser('eval', '''(async () => {
        const snapshot = await (await fetch(document.querySelector('#download-recipe').href)).json();
        if (snapshot.schema !== 1 || Object.keys(snapshot.documents).length !== 2) throw Error('Invalid export');
        return 'COMPOSITION_OK';
      })()''')
      assert 'COMPOSITION_OK' in result, result
      browser('set', 'viewport', '390', '844')
      browser('eval', "if (document.documentElement.scrollWidth > innerWidth) throw Error('Mobile overflow')")
      browser('fill', '#directory-search', 'no such recipe')
      browser('wait', '--fn', '!document.querySelector("#directory-empty").hidden')
      changed = catalog.data
      changed['generated_at'] = '2026-09-08T00:00:00Z'
      from index.registry import atomic_write
      from openmodels.contracts import dumps
      atomic_write(root / 'public/catalog.json', dumps(changed))
      browser('eval', 'document.querySelector("#compose-recipe").scrollIntoView({block:"center"})')
      browser('click', '#compose-recipe')
      browser('wait', '--fn', 'document.querySelector("#compose-status").textContent.includes("different catalog revisions")')
      browser('eval', 'if (!document.querySelector("#download-recipe").hidden) throw Error("Stale export remained visible")')
      print('Browser pagination, full-catalog search, history, CORS composition, export, revision mismatch and project Pages paths passed')
    finally:
      try:
        browser('close')
      finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
  main()
