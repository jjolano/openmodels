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
