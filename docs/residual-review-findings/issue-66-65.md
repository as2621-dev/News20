# Residual review findings — issues #66 + #65 (worker reads the same run flags as the script)

Built together: same file (`agents/worker/pipeline_routes.py`), same root cause (the deployed
worker did not mirror `scripts/run_live_batch.py`'s configuration). Adversarial lenses were run
inline (relay to spawned reviewers was unreliable this session); everything below is a residual
that was consciously ACCEPTED, not an unapplied fix.

## Decisions recorded

**#66 shortlist artifact destination — LOG-ONLY.** `scripts/run_live_batch.py` writes
`.agents/shortlists/<date>-shortlist.json`; the Railway worker has no repo checkout to write
into, and a Supabase table for a list that is read once would need a new migration. A halted
worker run emits one `pipeline_daily_shortlist_run` header event (the full #67 run envelope:
feed date, relevance mode, fell-back flag, counts) plus one `pipeline_daily_shortlist_entry`
per story. Per-entry lines rather than one blob because a 30-story list inside a single record
is the first thing a log viewer truncates. Swapping this for a table later touches only
`_log_shortlist_for_review` — never the halt.

**#65 cost note.** Arming the semantic key adds ONE batched `gemini-embedding-001` call per
worker run (N story + M interest embeddings). It is paid on halted runs too — deliberately: the
shortlist the founder reviews must be the list the two-key lock actually admits.

## Residuals

1. **A worker DEPLOY is required for either fix to take effect in production.** Until the
   Railway worker is redeployed, cron fires keep producing on the lexical key alone. Note that
   the deploy FLIPS the worker's default to halt-at-shortlist — that is a founder decision, not
   a build step, and was deliberately not performed by this slice.
2. **`SHORTLIST_ONLY=0` preserves today's produce path for #66 only.** #65 intentionally changes
   admission on the producing path as well (the semantic key now filters there too). This is the
   fix, not a regression — but "no behavior change on the approved path" is true of the shortlist
   flag alone, and any produced-volume drop after the deploy is expected, not a bug.
3. **`agents/worker/pipeline_routes.py` is 1200 lines**, over the CLAUDE.md 1000-line ceiling. It
   was already 1106 before this slice; splitting the route module is a separate refactor slice
   (Rule 3 — not folded into a spend-safety fix).
4. **X theme gather runs ABOVE the shortlist halt** (`RUN_X_THEMES=1`, off by default on both
   entry points), so a halted run with it on still renders tweet screenshots. Pre-existing and
   identical in `run_live_batch.py`; no Gemini spend involved. Left unchanged.
5. **The worker still constructs the TTS client (and, when posters are enabled, the genai image
   client) on a halted run.** Neither constructor spends — `GeminiTTSClient` builds its genai
   client lazily on first call — and skipping them would diverge from `run_live_batch.py`. The
   halt itself sits immediately above `_produce_story_pool` in `run_daily_pipeline` with nothing
   between, so production is unreachable when the flag is set.
6. **Flag-read asymmetry.** `shortlist_only_enabled()` is read once at the top of `_run_daily`;
   `semantic_relevance_key_enabled()` is read inside `ingest_fn` at invocation time. This mirrors
   `run_live_batch.py` exactly; within a single run the two are equivalent.
7. **`os.environ.get(name, "1") == "1"` was replaced by an explicit-falsy check.** The old
   spelling read `SHORTLIST_ONLY=true` as "produce" and `ENABLE_SEMANTIC_RELEVANCE_KEY=true` as
   "off" — both backwards. Both entry points now share `agents/pipeline/run_flags.py`, so an
   unrecognized value keeps the SAFE behavior on both.

## Verification

`tests/agents/worker/test_pipeline_run_flags.py` (7) + `tests/agents/pipeline/test_run_flags.py`
(12) pass, and were mutation-verified: dropping `llm_client`,
`enable_semantic_relevance_key` or `shortlist_only` fails 5 of them; reverting the `ingest_fn`
return to a 2-tuple fails 3. Suite: 1138 passed, 1 pre-existing unrelated failure
(`tests/agents/worker/test_interview_route.py::test_deeper_turn_returns_terminal_with_gemini_mocked`,
interview turn state machine — untouched by this slice). `ruff check` clean on all changed files.
Zero live API calls, zero deploy, zero batch run.
