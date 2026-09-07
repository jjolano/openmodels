"""Smoke-test built distributions outside the source checkout."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import venv
import zipfile


def check(directory, tag=''):
  wheels = sorted(Path(directory).resolve().glob('*.whl'))
  if len(wheels) != 2:
    raise ValueError('Expected SDK and runner wheels')
  for wheel in wheels:
    with zipfile.ZipFile(wheel) as archive:
      metadata = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
      version = next(line[9:] for line in archive.read(metadata).decode().splitlines() if line.startswith('Version: '))
      if tag and tag != f'v{version}':
        raise ValueError(f'{wheel.name} version does not match {tag}')
  with tempfile.TemporaryDirectory() as tmp:
    venv.create(tmp, with_pip=True)
    python = str(Path(tmp) / 'bin/python')
    sdk = next(w for w in wheels if w.name.startswith('openmodels-'))
    subprocess.run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', str(sdk)], check=True, cwd=tmp)
    subprocess.run([python, '-c', '''
import importlib.util
from openmodels import Catalog, Manifest, ModelStore
from openmodels.runner import Runner
assert importlib.util.find_spec('numpy') is None
assert importlib.util.find_spec('tinygrad') is None
assert importlib.util.find_spec('fastapi') is None
print('Dependency-free SDK imports outside checkout')
'''], check=True, cwd=tmp)
    runner = next(w for w in wheels if w != sdk)
    subprocess.run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', str(runner)], check=True, cwd=tmp)
    subprocess.run([python, '-c', 'import openmodels_runner_tinygrad'], check=True, cwd=tmp)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--dist', default='dist')
  parser.add_argument('--tag', default='')
  args = parser.parse_args()
  check(args.dist, args.tag)
