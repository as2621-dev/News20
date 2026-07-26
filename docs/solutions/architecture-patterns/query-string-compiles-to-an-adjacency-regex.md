---
title: A query string that compiles to a regex fails on the REGEX's rules, not the string's — and a "healthy" derivation hides it
tags: [interest-search-query, lexical-key, anchors, derive-anchor-specs, contiguity, silent-zero, hygiene-guard, backfill]
problem_type: pattern
symptoms: an ingestion run returns rows_returned 0 for EVERY interest with no error and
  no warning; interests look perfectly configured (non-null query, derives a strong
  anchor) yet match nothing; a hand-written anchor phrase that reads perfectly
  ("tariffs on China") never matches the headline it was written for
root_cause: interest_search_query is compiled into contiguous-phrase regexes — one per
  COMMA phrase, tokens joined by \s+ after sub-3-char/stopword words are dropped. A
  comma-less query therefore compiles to ONE anchor demanding the whole sentence
  adjacently, and a phrase with an interior filler word compiles to an anchor demanding
  its survivors be adjacent. Both are unmatchable while every existing health check passes.
date: 2026-07-26
---

## The two failures (issues #50 → #69)

`interest_search_query` is not matched as text — `derive_anchor_specs` **compiles** it:
split on commas, drop tokens < 3 chars and `_QUERY_STOPWORDS`, join the survivors with
`\s+`. So the string's readability tells you nothing about what will match.

1. **Comma-less soup.** `"humanoid robots robotics news"` compiles to
   `\bhumanoid\s+robots\s+robotics\b` — that exact word sequence, contiguously. It
   matches nothing, ever. The 2026-07-04 catalog seed wrote 75 such queries; on
   2026-07-25 the run returned `rows_returned: 0` across all 26 followed interests.
2. **Interior word drop.** `"tariffs on China"` compiles to `\btariffs\s+china\b` —
   `on` is under the 3-char floor, and the survivors are then required to be
   **adjacent**. The phrase can never match the headline it was written for. This was
   caught reviewing a hand-curated fix table, i.e. by someone who knew the rules.

## Why it was silent (the part worth remembering)

The existing health check (`has_hygiene_gap`) asks "is every anchor banned or
title-gated?". A soup mega-anchor is **multi-word**, so it is classed *strong* — the
interest scores as healthy precisely because its query is long. **A validity check
written over the derived artifact can be blind to a defect in the artifact's shape.**
Guard the shape too:

```python
@property
def has_uncommaed_soup_query(self) -> bool:
    if "," in self.source_query:
        return False
    return len(re.findall(r"[a-z0-9]+", self.source_query.lower())) >= 4
```

Two details that matter:

- **Keep the raw input on the derivation** (`source_query`) so the shape can be
  diagnosed at all. A derivation that discards its input can only ever check itself.
- **Count RAW words, not surviving tokens.** `"hurricane tropical storm news"` has only
  3 surviving tokens — filtering first hides it. The filler *is* the evidence that the
  author was listing keywords rather than writing a phrase.
- Threshold 4, not 3: `"Supreme Court ruling"` is a legitimate single anchor, and a
  guard that cries wolf on it gets ignored.

## Rules

- When a config string is compiled, **write the validator against the compiled form**.
  For every curated phrase, assert no word is dropped from BETWEEN two survivors
  (leading/trailing drops are harmless — `"Arsenal FC"` → `arsenal` is fine).
- **A separator is a contract.** If the consumer splits on commas, a value without
  commas is not "one big phrase", it is a bug. Guard for its absence explicitly.
- Adding a third failure mode to a shared log channel? Give **every** emission an
  explicit `hygiene_gap_kind` discriminator rather than letting readers infer the mode
  from which fields happen to be present — see [[side-band-signal-smuggled-into-shared-channel]].
- Backfilling the fix is curated data, not an algorithm: a short label cannot be
  algorithmically expanded into good anchors. Follow the `ROOT_QUERIES` precedent in
  [[deterministic-interest-query-backfill]] — a checked-in slug→query table, validated at
  plan time, failing loud on an unmapped broken row (never a guessed query, never an LLM).
- Watch the stopword list when writing anchors by hand: `market`, `markets`, `stock`,
  `stocks`, `team`, `news`, `fc` are dropped, so `"stock market"` compiles to **nothing**
  and `"carbon markets"` compiles to bare `carbon`.

Reference impl: `agents/ingestion/interest_lexical.py` (`has_uncommaed_soup_query`),
`scripts/seed_catalog/backfill_anchor_queries.py` (`validate_query`,
`_interior_dropped_words`), commit 67ea731.
