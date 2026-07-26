---
title: When a detector and a normalizer read the same field, normalize AFTER detecting
tags: [ingestion, gdelt, admission, html-entities, language-filter, composition, idempotence]
problem_type: pattern
symptoms: a gate that passed its own tests starts admitting rows it used to reject after
  an unrelated "cleanup" lands on the same field; a value is decoded/normalized twice and
  comes out as something the source never wrote; two filters that are individually correct
  are wrong in composition
date: 2026-07-25
---

Two slices landed on `_rows_to_candidates` within an hour: #63 (reject non-English
candidates) and #71 (clean the candidate title). Both read `row["title"]`, and both do
HTML-entity decoding — #63 internally, to *detect*; #71 to *store*. The ordering is not
a style choice, and getting it backwards is silent in tests that only exercise one slice.

**The rule.**

> A stage that DETECTS on a field must read the RAW value. A stage that REWRITES the
> field must run after it. Never the reverse.

Two independent failures come from cleaning first, and neither raises anything:

1. **The rewrite deletes the detector's evidence.** `河南GDP…机遇- CFi.CN 中财网` is
   rejected as non-Latin script. Strip the outlet suffix first and an English-looking
   remainder sails through the language gate — the *cleaner* silently widened admission.
   Any normalizer that removes text is capable of removing exactly the token the detector
   was keying on.
2. **Decodes stack.** `html.unescape` is only idempotent when the decoded output contains
   no `&…;` left. On a double-escaped title (`&amp;#x27;`, a literal the publisher
   actually printed) the second pass invents a character nobody wrote. So exactly ONE
   stage may own the decode-for-storage, and the detector's internal decode must stay a
   private, throwaway view of the raw value.

**How to keep it enforced:** put the ordering in a `# Reason:` comment at the call site,
and write the test at the *composition seam*, not in either pure module — assert that the
detector's verdict is unchanged for an input whose only evidence lives in the part the
normalizer would remove (`test_language_gate_still_reads_the_raw_title`). A test of each
module alone passes either way; only the seam test fails when someone reorders the lines.

**Corollary for the normalizer itself:** guard it with the same downstream gate it is
trying to satisfy, and make it fail-safe. #71's suffix strip is accepted only when the
remainder is still `is_publishable_headline` — the SAME classifier that would later drop
the story — so cleaning can never turn an admitted row into one that dies at persist. A
cleaner with no fallback is a new way to lose rows; dirty-but-valid always beats clean-
but-rejected.

See also: [[prefilter-must-be-looser-than-its-python-twin]] (the same "two stages, one
decision" family, split across SQL and Python).
