# Residual review findings — issue #47 (M1 live audit attempt, 2026-07-24)

Diff under review: `docs/ops/m1-live-audit-2026-07-24.md` +
`docs/solutions/operational-gotchas/embedding-outage-downgrades-run-to-lexical-only.md`.
Two adversarial lenses (correctness/logic, simplicity/reuse) ran over the drafts. All
critical/high/medium findings were **applied** before commit; the code-level ones became
issues #65, #66, #67. What follows is what was deliberately **not** applied.

## Deferred — advisory

- **`docs/solutions/README.md` does not document `operational-gotchas/`.** The Layout
  section lists `runtime-errors/ · performance-issues/ · security-issues/ ·
  architecture-patterns/ · conventions/ · tooling-decisions/`, and the `problem_type`
  enum omits `operational-gotcha` — yet 6 entries (oldest 2026-07-04) already use both.
  Because the Step-2.5 read is a frontmatter grep, an undocumented enum value hurts
  discovery of every entry in that directory. Not applied here: the divergence predates
  this slice and belongs to whoever owns the store's conventions (Rule 3 — don't tidy
  adjacent code inside an unrelated slice). One-line fix when someone touches the README.

## Applied (recorded so the next reviewer doesn't re-derive them)

- Dropped the committed `sha256(GEMINI_API_KEY)[:10]` + key length from the ops doc —
  secret-derived material does not belong in git history; `.env` mtime plus a local
  recipe answers "did the key rotate?" just as well.
- Corrected a false claim that the lexical fallback is invisible: it is logged loudly as
  `semantic_relevance_embed_failed`; only the *artifact* lacks the flag (→ #67).
- Scoped the "SHORTLIST_ONLY defaults to halt" claim to `scripts/run_live_batch.py` — the
  worker path ignores it entirely (→ #66).
- Scoped the "tests green" row: repo-wide pytest has 1 pre-existing unrelated failure
  (`test_interview_route.py::test_deeper_turn_returns_terminal_with_gemini_mocked`).
- Corrected "zero production credits" → shortlist mode still bills ingestion embeddings,
  the relevance-key embeddings and the LLM dedup judge; it removes the expensive tail.
- Fixed factual details: retry policy is 3 attempts / 2 sleeps (2s, 4s); the 07-20
  shortlist is 10/19 non-English, not ~7; "precisely the false-negative" softened to
  "consistent with, not proven" (the artifact carries no fallback flag); preflight block
  pasted verbatim; `date.today()` has no env override; the probe snippet made runnable.
- Renamed the learnings entry from the transient cause (credit depletion) to the
  mechanism (embedding outage → lexical-only), and trimmed it back to one fact.
