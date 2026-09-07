"""Build, verify, retain and install catalog publications."""
import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

from index.registry import atomic_write
from openmodels import Catalog, ContractError
from openmodels.contracts import dumps, sha256
from web.render import render


class Links(HTMLParser):
  def __init__(self):
    super().__init__()
    self.links = []

  def handle_starttag(self, tag, attrs):
    self.links.extend(value for key, value in attrs if key in ('href', 'src') and value)


def verify_site(root):
  root = Path(root)
  catalog = Catalog.load(root / 'catalog.json')
  pinned = Catalog.load(root / 'snapshots' / f'{catalog.revision}.json')
  if pinned.revision != catalog.revision:
    raise ValueError('Pinned snapshot differs from catalog')
  for digest, raw in catalog.data['documents'].items():
    if (root / 'manifests' / f'{digest}.json').read_text() != raw:
      raise ValueError(f'Manifest bytes differ: {digest}')
  for entry in catalog.data['entries']:
    recipe = Catalog.load(root / 'recipes' / f'{entry["recipe"]}.json').resolve(entry['recipe'])
    if recipe != catalog.resolve(entry['recipe']):
      raise ValueError('Recipe export differs from catalog')
  for page in root.rglob('*.html'):
    parser = Links()
    parser.feed(page.read_text())
    for link in parser.links:
      url = urlsplit(link)
      if url.scheme or url.netloc or not url.path:
        continue
      if url.path.startswith('/'):
        raise ValueError(f'Root-relative link breaks project Pages: {link}')
      if url.path == 'docs':  # Self-hosted API documentation, not a static file.
        continue
      target = (page.parent / unquote(url.path)).resolve()
      if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError(f'Broken site link: {link}')
  return catalog


def build(index, out, code, archive):
  render(Path(index), Path(out))
  catalog = verify_site(out)
  atomic_write(Path(out) / 'deployment.json', dumps({
    'schema': 1, 'code_revision': code, 'archive_revision': archive,
    'catalog_revision': catalog.revision,
    'api_base': os.environ.get('OPENMODELS_API_BASE', '').rstrip('/')}))
  return catalog


def retain(root, repo):
  catalog = verify_site(root)
  tag = f'catalog-{catalog.revision}'
  asset = Path(root) / 'snapshots' / f'{catalog.revision}.json'
  # Release creation is idempotent; never overwrite a digest-addressed asset.
  tags = subprocess.check_output(['gh', 'api', '--paginate', f'repos/{repo}/releases',
                                  '--jq', '.[].tag_name'], text=True).splitlines()
  if tag not in tags:
    subprocess.run(['gh', 'release', 'create', tag, '--repo', repo, '--latest=false',
                    '--title', f'Catalog {catalog.revision[:12]}', '--notes',
                    'Immutable catalog snapshot. Artifact licenses and provenance are recorded in its manifests.'], check=True)
  names = subprocess.check_output(['gh', 'release', 'view', tag, '--repo', repo,
                                   '--json', 'assets', '--jq', '.assets[].name'], text=True).splitlines()
  with tempfile.TemporaryDirectory() as tmp:
    metadata = (Path(root) / 'deployment.json').read_bytes()
    deployment = Path(tmp) / f'deployment-{sha256(metadata)}.json'
    deployment.write_bytes(metadata)
    existing = Path(tmp) / 'existing'
    existing.mkdir()
    for file in (asset, deployment):
      if file.name in names:
        subprocess.run(['gh', 'release', 'download', tag, '--repo', repo, '--pattern', file.name, '--dir', str(existing)], check=True)
        if (existing / file.name).read_bytes() != file.read_bytes():
          raise ContractError(f'Retained asset differs: {file.name}')
      else:
        subprocess.run(['gh', 'release', 'upload', tag, str(file), '--repo', repo], check=True)
  return catalog


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest='command', required=True)
  build_cmd = sub.add_parser('build')
  build_cmd.add_argument('--index', default='data/index.json')
  build_cmd.add_argument('--out', default='data/public')
  build_cmd.add_argument('--code', required=True)
  build_cmd.add_argument('--archive', required=True)
  keep = sub.add_parser('retain')
  keep.add_argument('--out', default='data/public')
  keep.add_argument('--repo', required=True)
  sync = sub.add_parser('sync')
  sync.add_argument('--url', required=True)
  sync.add_argument('--sha256')
  sync.add_argument('--data', default=os.environ.get('OPENMODELS_DATA', 'data'))
  args = parser.parse_args()
  if args.command == 'build':
    catalog = build(args.index, args.out, args.code, args.archive)
  elif args.command == 'retain':
    catalog = retain(args.out, args.repo)
  else:
    catalog = Catalog.load(args.url, expected_sha256=args.sha256)
    atomic_write(Path(args.data) / 'public' / 'catalog.json', catalog.raw)
  print(f'catalog {catalog.revision}: {catalog.search()["total"]} recipes')


if __name__ == '__main__':
  main()
