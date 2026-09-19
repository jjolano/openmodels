# OpenModels agent notes

This is an independent Git project. When working in the containing workspace,
also follow `../AGENTS.md` if present. The sibling moonpilot project owns model
admission and activation; changes to this archive do not grant consumer authority.

Read [the catalog contract](docs/catalog.md) before changing manifests, publication,
downloads or compiled builds. The first release uses schema 1 and package version 0.1.0.

## Scope and verification

- `openmodels/` owns the shared contracts, the SDK and the pinned profile/recipe.
  `index/` owns archive discovery, metadata, lineage and blob publication.
  `ci/qcom/` owns off-device a630 compilation, and `web/` renders the static directory.
  There is no server: the published catalog is a static snapshot.
- Use [README.md](README.md#check-changes) and `.github/workflows/test.yml` for
  dependency setup and checks. Importer changes use `python index/test_indexer.py`;
  metadata parsing uses `python index/test_metadata.py`. SDK, site and build changes
  use `python -m unittest discover -s tests -v`. Browser behaviour uses
  `python -m ci.browser`. Hardware parity and timing require separate device evidence.
- Keep `CLAUDE.md` as a relative symlink to this file. Preserve these contracts
  when updating workflow guidance.

## Pull requests

- `main` is protected: direct pushes are rejected and the required checks are strict. Land
  every change on a branch through a pull request, and squash-merge it once the `test` and
  CodeQL checks pass (`gh pr merge --squash --delete-branch`).
- A Dependabot pull request keeps its own branch: land a superseding change in your pull
  request and close the stale one.
- `github/codeql-action/init` and `github/codeql-action/analyze` share one version and move
  together; a split bump fails its own CodeQL check.

## Contracts and execution

- Identity and provenance are facts, not driving qualification. Equal tensor dimensions do
  not prove equal output semantics, channel order or temporal cadence.
- Hash exact UTF-8 document strings. Preserve those strings across exports and HTTP responses;
  reserializing a parsed object can change its identity.
- Keep every recorded source configuration. Missing constants stay absent, never default to
  zero. Merged output must stay byte-identical to the previous catalog for unchanged inputs.
- Reject known structural contradictions; report unknown structure and semantics as findings.
  An upstream pairing records co-occurrence, never qualification of a composed recipe.
- Keep the base SDK dependency-free. Downloads verify size and SHA-256 before atomic installation.
  Consumers own selection, activation, scheduling, qualification and actuation.

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
- Upstream LFS hosting moves: `index/lfs.py:BATCH_URL` mirrors openpilot's `.lfsconfig`
  (Hugging Face since 2026-09-09). The scan job compares the two with `check_lfsconfig` before
  discovery, so a moved store fails the refresh instead of marking reachable blobs unavailable;
  update the constant and its offline test together.
- Fetch blobs on demand and delete after upload. Serve blobs directly through storage/Caddy, not
  through Python.
- `archive-state` contains durable importer checkpoints. Save validated discoveries before
  mirroring or site deployment; use ordinary fast-forward commits and reject stale writers.
  Bootstrap from `gh-pages:index.json` only explicitly, preserving the existing archive.
- Pages consumes pinned code and archive commits. Rollback changes deployment, never archive
  history. Keep immutable catalog release assets; Actions artifacts alone expire.
- Keep read-only metadata inspection separate from publishing credentials. Before changing CI
  or deployment, read [deployment operations](docs/deployment.md), including synchronization and rollback.

## Precompiled a630 builds

- Compile only the pinned stock recipe, its exact artifact `659727c4…f8009b`, and the one
  recorded target. `ci/qcom/compile.py:check_target` is the only door; there is no CPU fallback
  and no other profile.
- Pin the toolchain by URL and SHA-256 in `ci/qcom/toolchain.py`, and keep the host LLVM
  version pinned by the `precompile` job. If upstream moves the toolchain, or the job's LLVM
  path changes, update the constants and the job together. Never fetch an unpinned compiler.
- The implementation digest includes the host LLVM by content, not by path. A different host
  LLVM must produce a different build, never a silently different artifact under the same id.
- Build identity covers the recipe document, the source artifact, the implementation digest and
  the toolchain digest. Never overwrite a release asset with different bytes: a differing
  same-name record raises, and only `builds.json` may be replaced in place.
- Keep `gpu_validated` and `device_validated` false until device evidence exists. Off-device
  compilation is not parity, timing or device qualification, and no record may imply otherwise.
- Vendored semantics must match the attributed pinned upstream sources; keep the derivation and
  the compiled-output note in `THIRD_PARTY_NOTICES.md`, and the drift check in
  `tests/check_reference.py`. Deserialize only artifacts this repository compiled.
