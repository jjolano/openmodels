"""Small SDK CLI: browse, export, compose and download without activating anything."""
import argparse
from pathlib import Path
import sys

from . import Catalog, ModelStore
from .contracts import dumps, loads


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--catalog", required=True, help="schema-1 snapshot file or URL")
  parser.add_argument("--sha256", help="expected snapshot byte digest")
  commands = parser.add_subparsers(dest="command", required=True)
  search = commands.add_parser("list")
  search.add_argument("--query", default="")
  search.add_argument("--offset", type=int, default=0)
  search.add_argument("--limit", type=int, default=100)
  resolve = commands.add_parser("export")
  resolve.add_argument("reference")
  compose = commands.add_parser("compose")
  compose.add_argument("request", help="JSON request matching /v1/compose")
  fetch = commands.add_parser("fetch")
  fetch.add_argument("reference")
  fetch.add_argument("--store", required=True)
  args = parser.parse_args()
  cat = Catalog.load(args.catalog, expected_sha256=args.sha256)
  if args.command == "list":
    print(dumps(cat.search(query=args.query, offset=args.offset, limit=args.limit)))
  elif args.command == "export":
    print(cat.export(cat.resolve(args.reference)))
  elif args.command == "compose":
    recipe, report = cat.compose(**loads(Path(args.request).read_bytes()))
    print(dumps(report), file=sys.stderr)
    print(cat.export(recipe))
  else:
    print(ModelStore(args.store, cat).fetch(cat.resolve(args.reference)).path)


if __name__ == "__main__":
  main()
