# Publisher submissions

Submit `<publisher>/<name>.json` as a pull request. The file is a schema-1 snapshot
containing exact profile/recipe document strings, artifact locations, source and license
information, and directory entries whose `publisher` matches the directory name.
The filename is a review convenience; full SHA-256 document IDs are the durable references.

Use `Catalog.export(recipe)` to produce a self-contained snapshot. Add a directory entry
with `name`, `publisher`, `kind`, `recipe`, and `occurrences`.
Validate with `Catalog.load(path)` before submitting. The publisher namespace records who
submitted the declaration; review and a digest do not establish execution qualification.

Unknown execution profiles are accepted as data. Installing a publisher submission never
installs runner code. Existing profile or artifact-location records cannot be overwritten
by a conflicting submission. New recipes may reference existing immutable documents.

`python -m index.registry --publishers publishers` validates submissions and merges them
with the comma archive. The generated schemas are published at `/schemas/*.json`, and the
same schemas are available at `/v1/schemas/{name}`.

## Named discovery

An entry can include `model` metadata with `id`, `name`, `family`, `description`, `links`
(an array of source URLs), and `archived` (boolean). Use a stable publisher-scoped ID such
as `example/model-name`; all entries sharing it must declare identical metadata. Their
exact recipe IDs remain separate selectable variants. Entries without metadata use their
publisher, kind and declared name for discovery grouping. Use distinct model IDs when
identically named packages should not share a group. `archived: true` includes a model in
the historical archive view. The main directory includes archived models by default. Optional `name_kind` is `published`, `source`, or `generated`. Optional `names` records
contain `name`, `source`, `url`, and `method` (`published` or `source`) for attributed
naming evidence and aliases. These fields affect discovery, never recipe identity.
No field implies consumer runtime support.
