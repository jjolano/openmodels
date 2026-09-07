# OpenModels agent notes

This is an independent Git project. When working in the containing workspace,
also follow `../AGENTS.md` if present. The sibling moonpilot project owns model
admission and activation; changes to this catalog do not grant consumer authority.

Read [the integration contract](docs/universal.md) before changing manifests, composition,
downloads or runner behavior. The first release uses schema 1, `/v1` and package version 0.1.0.

## Scope and verification

- `openmodels/` owns shared contracts, composition, the SDK, and runner interfaces;
  `api/` and `web/` expose them. Keep validation shared rather than duplicating it
  in each frontend.
- `index/` owns archive discovery, metadata, lineage, and blob publication.
  Read [publisher guidance](publishers/README.md) for publisher submissions.
- `runners/tinygrad/` is a separately packaged optional runner. Read the runner
  sections of [the integration contract](docs/universal.md) before changing it;
  retain pinned source attribution in `THIRD_PARTY_NOTICES.md`.
- Use [README.md](README.md#check-changes) and `.github/workflows/test.yml` for
  dependency setup and checks. Importer changes use `python index/test_indexer.py`;
  metadata parsing uses `python index/test_metadata.py`. SDK/API/runner changes
  use the relevant tests under `tests/`; run discovery for cross-cutting changes.
  Hardware parity and timing require separate device evidence.
- Keep `CLAUDE.md` as a relative symlink to this file. Preserve these contracts
  when updating workflow guidance.

## Contracts and execution

- Identity and provenance are facts, not driving qualification. Equal tensor dimensions do
  not prove equal output semantics, channel order or temporal cadence.
- Hash exact UTF-8 document strings. Preserve those strings across exports and HTTP responses;
  reserializing a parsed object can change its identity.
- Keep every recorded source configuration. Missing constants stay absent, never default to
  zero. Composition inherits only unanimous settings; explicit overrides enter recipe identity.
- Reject known structural contradictions; report unknown structure and semantics as findings.
  An upstream pairing records co-occurrence, never qualification of a composed recipe.
- Keep the base SDK dependency-free. Downloads verify size and SHA-256 before atomic installation.
  Consumers own selection, activation, scheduling, qualification and actuation.
- The optional runner accepts its pinned stock profile and complete target only. Deserialize
  executable builds only after verifying a trusted local receipt and implementation identity.
  Vendored semantics must match attributed pinned upstream sources. CPU primitive/reference
  checks do not qualify GPU compilation, output parity or timing.

## Comma archive

- Read full trees with `ls-tree`: a changed policy still belongs with its unchanged encoder.
  Retain every historical `MODEL_DIRS` entry. Match `big_` filenames before bare prefixes.
- Keep supercombo and split architectures separate, standard and big targets separate, and
  driving, dmonitoring and nav families separate.
- Status belongs to each occurrence. Internal bundle IDs hash role, filename and oid; a rename
  can carry meaning even with unchanged weights. An unreachable upstream ref must raise.
- Preserve previous `index.json` importer state, occurrence contexts and release mappings when
  PR refs disappear. Model blobs are append-only and may be the only surviving public copies.
- Keep ONNX inspection dependency-free and bounded. For attacker-controlled `output_slices`,
  use `metadata.loads_output_slices`, which permits only `builtins.slice`; preserve its malicious
  payload regression check. Never use unrestricted pickle loading on source metadata.
- Constant extraction searches historical source paths and both class and module bodies.
  When upstream moves, update `MODEL_DIRS`, `ROLE_PATTERNS` or `CONSTANT_SOURCES` while retaining
  historical entries. Missing extraction must remain visible.
- Blob release tags are recorded data, not derivable from a digest. Publish only recorded release
  URLs or verified local mirror paths; report unavailable and pending artifacts explicitly.
- Fetch blobs on demand and delete after upload. Publisher dry runs perform no network or repo
  mutations. Serve blobs directly through storage/Caddy, not through the Python API.
- `archive-state` contains durable importer checkpoints. Save validated discoveries before
  mirroring or site deployment; use ordinary fast-forward commits and reject stale writers.
  Bootstrap from `gh-pages:index.json` only explicitly, preserving the existing archive.
- Pages consumes pinned code and archive commits. Rollback changes deployment, never archive
  history. Keep immutable catalog release assets; Actions artifacts alone expire.
- Keep read-only metadata inspection separate from publishing credentials. Before changing CI
  or deployment, read [deployment operations](docs/deployment.md), including synchronization and rollback.
