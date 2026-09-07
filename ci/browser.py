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
      browser('click', 'a[href="#compose"]')
      browser('wait', '#recipe-slot-0')
      from openmodels import Catalog
      catalog = Catalog.load(root / 'public/catalog.json')
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
      print('Browser CORS composition, export, search, revision mismatch and project Pages paths passed')
    finally:
      try:
        browser('close')
      finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == '__main__':
  main()
