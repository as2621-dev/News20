# Slice #35 — residual review-panel findings (advisory / deferred)

Review panel ran on commit 00bc294 (3 lenses: correctness, simplicity, contract).
Concrete fixes were applied in the follow-up commit (depth-shift uniformity,
once-per-story log dedup, no-match warning→info doctrine, vacuous-assert removal,
drift-test comment-stripping + bidirectional completeness, adapter dedup). The
items below are deferred with reasons.

## Deferred to a new slice issue

- **HIGH (pre-existing TS bug, outside this slice's diff):**
  `src/lib/interestVector.ts` `INTEREST_ROOT_TO_PINNED_KEY` is missing identity
  entries for the post-SP3 roots `ai`, `politics`, `environment`, `arts` —
  profile rows rooted there return `null` from `pinnedKeyForInterestSlug` and are
  dropped from the interest vector, so `categoryBucketsFromInterestVector`
  reports "no backing" and the Thirty tab / Add-sheet treats those blocks as
  phantoms. Same drift class the new `TestTypescriptTwinDrift` automates, but in
  a TS file the test does not parse. Fix belongs with UI verification → filed as
  issue #43.

## Accepted with note (no fix)

- **LOW · `agents/pipeline/stages/ranking.py` `assign_category`:** a tag whose
  slug root is absent from `SLUG_TO_CATEGORY` still resolves to arts silently via
  `category_for_slug`'s default (resolvable-but-unmapped-root path). Pre-existing;
  the no-tag and no-theme paths are now loud, and an unmapped ROOT in a followed
  interest would already fail the drift test if it were a picker root. Revisit if
  non-picker roots ever enter the taxonomy.
- **LOW · `scripts/theme_miss_counts.py`:** duplicates `_INTEREST_COLS`/`_node`
  from `scripts/run_live_batch.py` — importing that module would drag its heavy
  module-level deps (genai/TTS clients) into a read-only evidence script; the
  duplication is deliberate. Inline whitelist membership (`any(t in
  THEME_CATEGORY_WHITELIST ...)`) is kept instead of `category_for_themes(...) is
  None` to avoid emitting ~900 log lines during a tally run; if matching ever
  goes prefix-based, update the script with it.
- **LOW · Supabase reads in the evidence script are unpaginated** (default 1,000
  rows): truncation is visible in the printed `interests=`/`followed_rows=`
  counts (2026-07-07 run: 237/55 — no truncation); docstring carries the caveat.
