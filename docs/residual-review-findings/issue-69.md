# Residual review findings — issue #69 (comma-anchor query backfill + soup guard)

Reviewed inline (correctness of anchor generation, guard threshold, dry-run safety,
contract with #50's `derive_anchor_specs`). Fixed during review, not left residual:

- **`"tariffs on China"` / `"sanctions on Russia"` were unmatchable anchors.** The key
  drops sub-3-char words and then joins the survivors with `\s+`, so the regex demanded
  `tariffs china` **adjacent** — it could never match the headline it was written for.
  Fixed both table entries and added `_interior_dropped_words` as a hard `validate_query`
  rule (+ tests) so the whole class cannot recur.

## Residuals (accepted, not fixed here)

1. **Weak anchors for abstract policy interests.** `ai.china`, `ai.us-policy`,
   `ai.global-governance`, `ai.eu-ai-act` and `arts.trends` get literal phrase anchors
   ("China artificial intelligence", "arts trends") that real headlines rarely use
   verbatim ("China's AI push"). They are strictly better than the soup they replace
   (which matched nothing), but they will be low-recall. Strengthening them means adding
   entity anchors (DeepSeek, Alibaba; the AI Act's article numbers) — a curation call
   flagged for the founder in the #69 dry-run comment rather than invented here.

2. **~36 catalog interests are contract-compliant but thin** (one anchor each, e.g.
   `arts.photography` → `photography`, `sport.golf` → `golf`). They are precise and DO
   match, so they are out of this slice's scope (which is unmatchable queries), but they
   have a single way in. Worth a follow-on enrichment slice.

3. **`business.equities` and `business.stocks-equities` are duplicate catalog nodes** and
   receive the identical query. The duplication predates this slice; de-duping the
   catalog is a separate change.

4. **The DOC path (`anchor_scalpel.split_anchor_terms`) is not guarded.** The soup
   warning lives at the `derive_anchor_specs` call site (the BigQuery batch path, which
   is the production path). A soup query on the DOC path yields one long exact-phrase
   query — degraded but not silent-zero in the same way. Not in scope for #69.

5. **The guard will emit 77 warnings per batch until the backfill applies.** That is the
   intended loudness (Rule 12), but it is noise on every run until the founder-gated
   apply lands.
