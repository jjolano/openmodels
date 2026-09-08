import contextlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ci import archive
from ci.site import build, verify_site
from openmodels import Catalog
from openmodels.contracts import dumps


def source():
  return {'schema': 1, 'generated_at': '2026-09-07T00:00:00Z', 'upstream_head': 'commit',
           'files': [{'oid': 'a' * 64, 'size': 1}],
          'bundles': [{'bundle_id': 'one', 'name': 'Archived model', 'kind': 'driving', 'variant': 'standard',
            'files': [{'oid': 'a' * 64, 'size': 1, 'filename': 'supercombo.onnx', 'role': 'supercombo'}],
            'occurrences': [{'commit': 'one', 'status': 'merged', 'date': '2026-09-07T00:00:00Z'}]}]}


class PublicationTests(unittest.TestCase):
  def test_checkpoint_survives_failed_site_and_rejects_stale_writer(self):
    with tempfile.TemporaryDirectory() as root:
      remote, work = Path(root) / 'remote.git', Path(root) / 'work'
      subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
      work.mkdir()
      with contextlib.chdir(work):
        archive.git('init', '-b', 'main')
        archive.git('config', 'user.name', 'test')
        archive.git('config', 'user.email', 'test@example.com')
        archive.git('remote', 'add', 'origin', str(remote))
        path = Path('index.json')
        path.write_text(dumps(source()))
        archive.git('add', 'index.json')
        archive.git('commit', '-m', 'Original archive')
        archive.git('push', 'origin', 'HEAD:refs/heads/gh-pages')
        with self.assertRaisesRegex(ValueError, 'missing'):
          archive.restore(Path('data/index.json'))
        self.assertEqual(archive.restore(Path('data/index.json'), bootstrap=True), '')
        first = archive.checkpoint(Path('data/index.json'), '')
        self.assertEqual(archive.checkpoint(Path('data/index.json'), first), first)
        lost = source()
        lost['files'], lost['bundles'] = [], []
        Path('data/index.json').write_text(dumps(lost))
        with self.assertRaisesRegex(ValueError, 'discard'):
          archive.checkpoint(Path('data/index.json'), first)
        data = source()
        data['generated_at'] = '2026-09-08T00:00:00Z'
        Path('data/index.json').write_text(dumps(data))
        second = archive.checkpoint(Path('data/index.json'), first)
        self.assertEqual(archive.git('rev-parse', f'{second}^'), first)
        with self.assertRaisesRegex(ValueError, 'advanced'):
          archive.checkpoint(Path('data/index.json'), first)
        # Site failure and rollback read an old checkpoint without moving durable archive HEAD.
        with self.assertRaises(FileNotFoundError):
          build('missing.json', 'site', 'code', first)
        archive.restore(Path('rollback.json'), revision=first)
        self.assertEqual(json.loads(Path('rollback.json').read_text()), source())
        self.assertEqual(archive.remote_head(), second)

  def test_real_publishers_and_repeatable_site(self):
    with tempfile.TemporaryDirectory() as root:
      root = Path(root)
      index = root / 'index.json'
      index.write_text(dumps(source()))
      first = build(index, root / 'one', 'code', 'archive')
      second = build(index, root / 'two', 'code', 'archive')
      self.assertGreater(first.search()['total'], 0)
      self.assertEqual(first.revision, second.revision)
      for file in (root / 'one').rglob('*'):
        if file.is_file():
          self.assertEqual(file.read_bytes(), (root / 'two' / file.relative_to(root / 'one')).read_bytes())
      info = json.loads((root / 'one/deployment.json').read_text())
      self.assertEqual(info['catalog_revision'], first.revision)
      home = (root / 'one/index.html').read_text()
      self.assertIn('id="pipeline"', home)
      self.assertNotIn('data-directory-entry', home)
      self.assertNotIn('data-model=', home)
      self.assertTrue((root / 'one/models.html').is_file())
      self.assertEqual(home, (root / 'one/compose.html').read_text())
      (root / 'one/index.html').write_text('<a href="/catalog.json">broken project path</a>')
      with self.assertRaisesRegex(ValueError, 'project Pages'):
        verify_site(root / 'one')


  def test_snapshot_retention_is_idempotent_and_verifies_existing_bytes(self):
    from ci.site import retain
    from openmodels import ContractError
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      index = root / 'index.json'
      index.write_text(dumps(source()))
      catalog = build(index, root / 'public', 'code', 'archive')
      assets, tags, uploads = {}, [], []
      def query(args, **kwargs):
        return '\n'.join(tags if args[1] == 'api' else assets)
      def command(args, **kwargs):
        self.assertNotIn('--clobber', args)
        if args[2] == 'create':
          tags.append(args[3])
        elif args[2] == 'upload':
          file = Path(args[4])
          assets[file.name] = file.read_bytes()
          uploads.append(file.name)
        elif args[2] == 'download':
          directory = Path(args[args.index('--dir') + 1])
          name = args[args.index('--pattern') + 1]
          (directory / name).write_bytes(assets[name])
      with patch('ci.site.subprocess.check_output', side_effect=query), patch('ci.site.subprocess.run', side_effect=command):
        retain(root / 'public', 'owner/repo')
        retain(root / 'public', 'owner/repo')
        self.assertEqual(uploads[0], catalog.revision + '.json')
        self.assertEqual(len(uploads), 2)
        self.assertTrue(uploads[1].startswith('deployment-'))
        assets[uploads[0]] += b' '
        with self.assertRaises(ContractError):
          retain(root / 'public', 'owner/repo')

  def test_api_catalog_precondition(self):
    from fastapi.testclient import TestClient
    from api.main import app
    from test_universal import fixture
    data, recipe, _ = fixture()
    catalog = Catalog(dumps(data))
    request = {'profile': recipe.profile.id, 'selection': {
      role: {'recipe': recipe.id, 'slot': role} for role in ('head', 'encoder')}}
    with patch('api.main.catalog', return_value=catalog), TestClient(app) as client:
      self.assertEqual(client.post('/v1/compose', json=request,
        headers={'If-Match': '"' + '0' * 64 + '"'}).status_code, 412)
      self.assertEqual(client.post('/v1/compose', json=request,
        headers={'If-Match': f'"{catalog.revision}"'}).status_code, 200)
      self.assertEqual(client.post('/v1/compose', json=request).status_code, 200)


if __name__ == '__main__':
  unittest.main()
