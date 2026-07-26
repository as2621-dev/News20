# Residual review findings — issue #71 (title hygiene at GDELT admission)

Commits: `b3f2208` (slice), `b460df9` (review-panel fix).
Panel: run inline (correctness of the suffix strip + fragment guard, idempotence of the
decode, composition with #63, contract/twin drift, Rule 12 visibility). Concrete fixes
were applied during the build; what follows is what was **accepted, not fixed**.

## Applied during review (not residual)
- Prefix-match keys length-gated to 4 chars (`b460df9`) — `art.com` would have eaten
  "… - Art of the deal, explained". Exact matches still fire at 3 (`… - CNN`).
- Site-label word list trimmed to the evidenced core: `today`, `network`, `media`,
  `wire`, `online` removed (each doubles as an ordinary headline noun — "Best Deals
  Today", "Neural Network" — and the outlets that use them are caught by their domain).
- Registry/section labels (`com`, `net`, `news`, `co`…) excluded from outlet matching.
- Aggregate `gdelt_candidate_titles_cleaned` log added (Rule 12): the gate REWRITES what
  the founder reviews, so it must not be invisible.
- `CandidateStory.candidate_title` docstring corrected — it is no longer the raw
  `<PAGE_TITLE>`.

## Residual 1 — no backfill of titles already persisted
Admission-time only. Rows already in `stories.canonical_title` keep their entities and
suffixes; the fix reaches them only on re-ingest. The 2026-07-25 shortlist artifacts are
historical records and were deliberately not rewritten.
**Accepted:** out of the slice's scope, and the next live batch re-admits fresh rows.
File a backfill slice if dirty legacy titles surface in a produced feed.

## Residual 2 — the site-label rule is a shape heuristic
`- Ipswich Town News` is caught because the tail is short, title-cased and ends in a site
label — not because we know that string is an outlet. A trailing clause with the same
shape ("… - Sunday Sport") would be stripped. Bounded by three conditions (separator,
≤4 title-cased words, remainder still publishable) and it stripped 8/139 real rows with
zero over-strips, but it is a heuristic, not a fact.
**Accepted:** extend `_SITE_LABEL_WORDS` on audit evidence only; the aggregate log is the
surface where an over-eager strip becomes visible.

## Residual 3 — AC1 as literally written is unsatisfiable
The issue's happy path spells `A&#xA0;B : Bollywood News` → `A B`, but `A B` is a
two-word fragment and the same issue forbids creating one. The guard wins (a fragment is
dropped outright at persist; a dirty-but-publishable title is not). Both behaviours are
encoded as tests: `test_non_breaking_space_becomes_a_real_space` (realistic length) and
`test_two_word_remainder_loses_to_the_fragment_guard` (the literal string).

## Residual 4 — non-Latin titles keep their suffix
Row 54 (`…机遇- CFi.CN 中财网`) decodes but keeps its suffix: the remainder is two
whitespace-delimited "words", which `headline_rejection_reason` calls `too_few_words`.
The word-count floor is Latin-script reasoning applied to CJK.
**Accepted:** #63 rejects those rows at admission anyway, so the path is dead in
practice. Revisit only if blip ever ships a non-English surface.

## Residual 5 — the DOC adapter cleans without an aggregate log
`gdelt_doc._article_to_candidate` cleans per-article and has no batch seam to log from,
so its rewrites are not counted. It is the low-volume scalpel path (the BigQuery
workhorse carries the batch), so the visibility gap is small.
**Accepted:** revisit if DOC ever becomes a bulk source.

## Pre-existing, disclosed, not touched
- `agents/ingestion/adapters/gdelt_doc.py` fails `ruff format --check` on a hunk that
  predates this slice (`build_domain_query`, line ~65). Verified pre-existing by
  stashing; left alone (Rule 3 — clean up only your own mess).
- `tests/agents/worker/test_interview_route.py::test_deeper_turn_returns_terminal_with_gemini_mocked`
  fails on `main` and still fails; unrelated to ingestion.
