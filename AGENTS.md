# openmodels — agent notes

A public archive of openpilot driving models, indexed from `commaai/openpilot` git history.

Read this before changing `index/`. Everything below is a decision where **the obvious change is
the wrong one** — the repo itself won't tell you.

## The boundary

This registry reports **identity and provenance**. It never asserts that a model is safe, or
that two models are interchangeable.

That is not modesty, it is what the data supports. Two models can share every tensor shape and
slice width while meaning entirely different things: column order inside the 990-wide `plan`
slice is hard-coded in openpilot's `constants.py`, not declared in the file, and permuting it
leaves any structural hash unchanged. MDN field ordering, YUV channel order, and temporal cadence
are equally invisible.

**Do not add a `/v1/compat` endpoint, a "compatible" flag, or a similarity score that reads as a
safety verdict.** An earlier design did, and it would have told a QCOM device that a 296 MB
USBGPU-only model was an exact match. Compatibility is expressed as provenance — *this ran
upstream at this commit with these constants* — which a client can independently re-verify.

## Never unpickle `output_slices`

It is a base64 **pickle** inside attacker-controllable ONNX metadata, and the indexer reads
arbitrary PR heads while CI holds publish credentials. `pickle.loads` there is remote code
execution via a crafted pull request.

Use `metadata.loads_output_slices`, which resolves nothing but `builtins.slice`. If you need a
new type out of that stream, widen the allowlist deliberately and add a test — do not reach for
`pickle.loads`. `index/test_metadata.py` carries a live `os.system` payload that must stay
refused.

## Host constants are the payload, and absence is data

`LAT_SMOOTH_SECONDS` / `LONG_SMOOTH_SECONDS` feed lateral delay in `controlsd`, and comma changes
them *in the same commit that swaps a model*. Weights without them are an incomplete artifact.

When extraction fails, emit `null` and record it in `host_constants_missing`. **Never default to
zero.** A wrong smoothing constant silently changes steering, which is worse than a visibly
absent one. Old eras genuinely lack these constants and correctly report them missing.

## Indexer invariants

- **`ls-tree`, never `diff`.** Bundles are the full file set *at* a commit. A commit touching
  only `driving_on_policy.onnx` must still produce a complete bundle; diffing yields unrunnable
  half-bundles.
- **Three path eras**, all in `MODEL_DIRS`: repo-root `models/`, `selfdrive/modeld/models/`, and
  the post-#38223 `openpilot/selfdrive/modeld/models/`. Dropping one silently loses models —
  the pre-2022 era alone holds the nav models.
- **A supercombo never shares a bundle with a vision/policy split.** Both exist at the transition
  commit and they are different architectures. Same for `big_` (USBGPU/AMD) vs standard (QCOM),
  and for driving vs dmonitoring vs nav.
- **Status belongs to an occurrence, not to content.** One file set can be merged, reverted, and
  re-landed. A single `status` on the bundle would be incoherent.
- **Bundle ids hash `(role, filename, oid)`**, not oids alone: commit `249cafe` renamed
  `driving_policy` to `driving_on_policy` without changing bytes, and that rename carries runtime
  meaning.
- **An unreachable ref must raise**, never be treated as "no models found". `index_repo` checks
  `repo.exists(head)` first, because the two look identical downstream.

## Other standing decisions

- The ONNX parser stays **dependency-free**. Reaching for `onnx` or `tinygrad` looks like a
  simplification and costs the "runs on any CI runner, no comma hardware" property.
- Blobs are **append-only**. For reverted and PR-only models this archive may be the only public
  copy; a cleanup that GCs them is unrecoverable.
- Blobs **never stream through Python** — both backends 302 to a URL, so a dead API costs live
  queries only.
- **Which release holds a blob is data, not a formula.** GitHub caps a release at 1000 assets,
  so `index/publish.py` shards across `blobs-NNNN` and writes the tag onto each file. Never
  reconstruct a download URL from the oid alone. A file with no `release` isn't mirrored yet and
  the API returns 503 — do not "fix" that by redirecting to a URL that 404s.
- **The publisher fetches on demand and deletes after upload.** Never pre-download the archive
  in CI: it's ~8 GB, exceeds the Actions cache, and steady-state runs would move it for nothing.
- **`--dry-run` must touch neither the network nor the repo.** It once uploaded for real when a
  blob happened to be cached; keep the guard above the fetch, not inside it.
- `gh-pages` is generated and force-pushed. Never hand-edit it or merge into it.
- **No subjective data.** No ratings, comfort scores, or steering feel — we have no telemetry and
  would be inventing them. Link to sunnylink.wiki for that.

## When upstream moves

This is the recurring maintenance event, and its failure mode is **silence**: the indexer keeps
succeeding while finding nothing, so "no new models" looks exactly like "upstream shipped
nothing."

1. Check `/v1/status` — a stale `generated_at` with a fresh `upstream_head` is the signature.
2. If a model path moved, add it to `MODEL_DIRS` (keep the old entries; history still needs them).
3. If a constant moved or was renamed, add the new path to `CONSTANT_SOURCES` in
   `index/constants.py`. Watch for it moving between module level and a class body — both are
   searched, but a rename is invisible.
4. If a model filename changed, add it to `ROLE_PATTERNS` **above** any prefix it would otherwise
   match; `big_` entries are checked before their bare equivalents.

## `runtime/` — the integration library

Sits **outside the control path** by design: it plans compiles and manages downloads, but never
runs inference, parses model outputs, or touches actuation. Keep it that way. Output semantics
(meta layout, MDN field order, desire encoding) belong to the fork's `modeld`, and reimplementing
them here would create a second, unvalidated source of safety-relevant behaviour. If that
knowledge is ever needed, vendor openpilot's or sunnypilot's parsers with attribution rather than
writing new ones.

- **Map roles to compiler flags, never filenames.** `driving_policy.onnx` and
  `driving_on_policy.onnx` are the same role in different eras.
- **Duck-type input keys**, following sunnypilot: prefix/substring matching absorbs
  `input_imgs` → `img` and `desire` → `desire_pulse`. Exact-name lookups break on every model
  older than the current era.
- **`frame_skip` and `model_size` are host properties**, not model properties — scons derives
  them from the fork's own constants. Report a disagreement with the model's recorded value as a
  warning; never silently pick one.
- **Downloads stage then move atomically.** A half-finished bundle that reads as installed is
  worse than one that failed outright.

- **`build.py` orchestrates a compile; it never runs inference.** The smoke test checks that the
  artifact exists, unpickles, and carries `metadata` — nothing more. Executing a driving model to
  "validate" it means interpreting its outputs, which is the boundary above.
- **Compilation is fail-loud, which is why it can be automated.** No pickle, or an unloadable
  one, is a visible failure; keep it that way by staging, verifying, then moving atomically, and
  by restoring the previously active model on any error.
- **Do not try to validate compiles on CPU in CI.** On the pinned tinygrad the CPU JIT fails to
  link libm (`fmaxf`), reproducibly in a clean ubuntu:24.04 container; patching `link_libs=['m']`
  then overflows the 32-bit relocation because the plain path passes no base address. Upstream
  pins no clang and compiles no models in CI, so nobody exercises this path. Re-test only if the
  tinygrad submodule bumps.

## Lineage and composition

`model_checkpoint` records the training runs behind a model, and a fused supercombo names both
of its halves (`<vision_ckpt>/<step>/<policy_ckpt>/<step>`). That is what makes "these halves
were built for each other" a **fact comma recorded**, not an inference — which is the only reason
this feature is allowed to exist under the boundary above.

- **Attested means it shipped.** A pairing is attested iff those checkpoints co-occurred in an
  upstream bundle, or a supercombo named them together. Never widen this to "same shapes" or
  "same generation" — that is the inference the registry refuses to make.
- **Composed bundles are never attested.** `attested: false` is unconditional on anything from
  `/v1/compose`: it may be assembled from halves that shipped together, but *this file set* has
  never been driven.
- **Cross-lineage composes and warns; it does not fail.** The vision→policy latent is untyped, so
  a mismatched encoder produces confident nonsense rather than an error. The failure is silent,
  so the warning cannot be. Refuse only what cannot work: unknown oid, unusable role set, or a
  seam width mismatch.
- **Composition is stateless.** `bundle_id` is derived from members, so nothing is stored. Do not
  add a database to make composed models browsable — that would put our name on combinations
  nobody ran.
- **Host constants stay per-role.** Halves come from different commits; merging their constants
  invents a configuration that never existed. Report each and let the fork choose.
- **Lineage requires a metadata pass.** The default indexer run reads only LFS pointers, so
  `model_checkpoint` is absent until a `--blob-cache` run. Use `--metadata-source releases` to
  read from our own mirror rather than hammering comma's LFS.

## Shareable composition codes

`OM2-…` codes carry truncated oids and are resolved against the catalog, which makes a code a
**lookup key, not a description**. That is the safety property: a damaged code fails to resolve
or fails its checksum, and cannot quietly name different weights — the only failure mode that
would matter on a device.

- **Redemption is where validation happens.** The code asserts nothing. `/v1/compose/{code}` and
  `runtime.manager.redeem_code` both re-resolve and re-run every check. Never trust a code's
  contents without resolving them.
- **`SHAPES` in `index/code.py` is append-only**, and roles within a shape are the encoding
  order. Reordering either silently changes what old codes mean, which is exactly the
  misresolution the format exists to prevent.
- **The format is squeezed for touchscreen entry** (13 typed characters): the role *set* is one
  5-bit shape rather than a byte per role, oid prefixes are 3 bytes, and the checksum is 1 byte
  because prefix resolution already rejects nearly all corruption. Do not lengthen these without
  a reason; do not shorten the oid prefix further, since ambiguity becomes likely rather than
  merely detectable.
- **The `OM2-` prefix is optional on input.** It exists for human recognition, not for the
  format; nobody should thumb in three extra characters.
- **The encoder exists twice** — Python in `index/code.py`, JavaScript in `web/render.py`'s
  `COMPOSE_JS`, because the compose page is static and must work without the API. Golden vectors
  in `index/test_compose.py` pin both; they were verified equal by driving the page's JS under
  node. If you change the format, change both and update the vectors.
- **Codes differing only in trailing base32 padding bits are the same code.** Encoding always
  emits the canonical form; accepting variants is fine.
