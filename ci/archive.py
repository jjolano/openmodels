"""Read and checkpoint importer state independently of website deployment."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from index.registry import convert
from openmodels import Catalog
from openmodels.contracts import dumps


def git(*args, **kwargs):
  return subprocess.check_output(['git', *args], text=True, **kwargs).strip()


def remote_head():
  # A successful empty result means absent; transport/authentication failures must raise.
  result = git('ls-remote', '--heads', 'origin', 'refs/heads/archive-state')
  return result.split()[0] if result else ''


def read(ref):
  if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/-]*', ref) or '..' in ref:
    raise ValueError('Invalid archive revision')
  git('fetch', '--no-tags', 'origin', ref)
  revision = git('rev-parse', 'FETCH_HEAD')
  data = json.loads(git('show', f'{revision}:index.json'))
  Catalog(dumps(convert(data)))
  return revision, data


def restore(path, revision='', bootstrap=False):
  head = remote_head()
  if revision:
    selected, data = read(revision)
  elif head:
    selected, data = read(head)
  elif bootstrap:
    _, data = read('gh-pages')
    selected = ''
  else:
    raise ValueError('archive-state is missing; run Refresh archive with bootstrap enabled to preserve gh-pages:index.json')
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(dumps(data) + '\n')
  return selected


def checkpoint(path, expected):
  if remote_head() != expected:
    raise ValueError('Archive advanced since restore; rerun the refresh')
  data = json.loads(path.read_text())
  if expected:
    _, previous = read(expected)
    for key, identity in (("files", "oid"), ("bundles", "bundle_id")):
      records = {record[identity]: record for record in data[key]}
      for old in previous[key]:
        current = records.get(old[identity])
        if current is None:
          raise ValueError(f"Checkpoint would discard archived {key}: {old[identity]}")
        if key == "bundles":
          for history in ("occurrences", "host_contexts"):
            before = {item["commit"] for item in old.get(history, [])}
            after = {item["commit"] for item in current.get(history, [])}
            if not before <= after:
              raise ValueError(f"Checkpoint would discard {history}")
        elif any(field in old and field not in current for field in ("release", "metadata")):
          raise ValueError("Checkpoint would discard artifact metadata or mirror placement")
  Catalog(dumps(convert(data)))
  raw = dumps(data) + '\n'
  path.write_text(raw)
  with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index'),
           'GIT_AUTHOR_NAME': 'openmodels', 'GIT_AUTHOR_EMAIL': 'actions@users.noreply.github.com',
           'GIT_COMMITTER_NAME': 'openmodels', 'GIT_COMMITTER_EMAIL': 'actions@users.noreply.github.com'}
    blob = git('hash-object', '-w', '--stdin', input=raw)
    git('read-tree', '--empty', env=env)
    git('update-index', '--add', '--cacheinfo', f'100644,{blob},index.json', env=env)
    tree = git('write-tree', env=env)
    if expected and tree == git('rev-parse', f'{expected}^{{tree}}'):
      return expected
    parents = ['-p', expected] if expected else []
    commit = git('commit-tree', tree, *parents, '-m', 'Checkpoint model archive', env=env)
    # Ordinary fast-forward push: a competing writer causes failure, never history replacement.
    git('push', 'origin', f'{commit}:refs/heads/archive-state')
    return commit


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('command', choices=['restore', 'checkpoint'])
  parser.add_argument('--index', type=Path, default=Path('data/index.json'))
  parser.add_argument('--revision', default='')
  parser.add_argument('--bootstrap', action='store_true')
  args = parser.parse_args()
  revision = (restore(args.index, args.revision, args.bootstrap) if args.command == 'restore'
              else checkpoint(args.index, args.revision))
  if output := os.environ.get('GITHUB_OUTPUT'):
    with open(output, 'a') as handle:
      handle.write(f'revision={revision}\n')
  print(revision)


if __name__ == '__main__':
  main()
