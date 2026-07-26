# Residual review findings — issue #63 (English-only language filter at GDELT admission)

**Status: panel run INLINE by the build agent** (four lenses: SQL⇄Python twin
correctness, accented-English false negatives, fail-open visibility,
contract/architecture). Spawned-reviewer relay was unreliable this session, so the
lenses were run directly rather than parking the issue at `status:review`. Both
concrete defects found were FIXED before the commit; the rest are residuals below.

## Fixed during review (in the shipping commit)

| # | Finding (lens, severity) | Outcome |
|---|---|---|
| 1 | **SQL was STRICTER than its Python twin** (correctness, MED-HIGH). `_ENGLISH_SOURCE_CODES` accepts `{"eng", "en"}` but the emitted SQL compared `= 'eng'`. A row stamped `srclc:en` would have been dropped in BigQuery before the Python half ever saw it — an over-rejection with no log line anywhere (the SQL runs first and its rejections are unrecoverable). | FIXED — `build_language_filter_sql` now generates `IN ('en', 'eng')` **from** `_ENGLISH_SOURCE_CODES` (sorted), so the two cannot diverge. Pinned by `TestSqlTwin::test_sql_accepts_every_code_python_accepts`. |
| 2 | **Accent-density rule dropped English name headlines** (false-negative, MED). "Beyoncé, Céline win Grammys" has two accented words and fewer than two English function words → rejected. Same for "Pokémon Café opens". | FIXED — only **lowercase** accented words count toward the density rule. Accents confined to capitalized (proper-noun) positions are names, not foreign prose; lowercase mid-sentence accents ("programy zaczęły raportować") still reject. Both directions pinned (`test_two_accented_names_admit_with_no_english_function_words`, `test_lowercase_accented_prose_still_rejects`). |

## Residuals (accepted, not fixed)

1. **`TranslationInfo` column existence is UNVERIFIED against the live table
   (HIGH-visibility, low-probability).** The column name comes from the GKG 2.1
   spec — the same field list this adapter already reads `Extras`, `SharingImage`
   and `V2Persons` from — but the zero-live-API guardrail for this slice forbade a
   dry-run. If the name is wrong the **first live batch fails LOUDLY** with
   `AdapterFetchError` carrying BigQuery's "Unrecognized name: TranslationInfo"
   (never silently) and the fix is a one-word rename. **Watch the next live run.**
   NULL/blank values are already safe: `IFNULL(..., 'eng')` fails open.

2. **The rejection log names the reason, not the row.** Rejections are aggregated
   per call (`gdelt_non_english_candidates_rejected` with a `{reason: count}` map)
   because per-row logging of a 5000-row batch buries the signal. Consequence: you
   cannot tell from the log WHICH title was dropped, only how many and why. If a
   false-negative hunt ever needs that, add a sampled `rejected_titles[:5]` field.

3. **The Python backstop runs AFTER the per-interest top-K window.** The
   authoritative srclc rule is in SQL (pre-JOIN, so translated docs never consume a
   slot), but the title-side rules run in `_rows_to_candidates` on returned rows —
   so a foreign-language page that GDELT filed in its *English* feed still eats one
   top-K slot before being dropped. Bounded and rare; moving the title rules into
   SQL would mean re-implementing HTML-entity decoding and Unicode script analysis
   in RE2, which is where the twin would actually drift.

4. **`search()` (the recall path) is now English-only too.** Same
   `_rows_to_candidates`, so the BigQuery recall path drops non-English rows. In
   production the coverage census injects the **DOC** adapter
   (`build_coverage_report`'s signature is `GdeltDocAdapter`), so outlet-spread
   counting is unaffected; the only BigQuery `search()` callers are the ABC contract
   and `scripts/theme_miss_counts.py`. If the census is ever switched to BigQuery,
   revisit — foreign coverage IS a legitimate spread signal even for an English feed.

5. **`_FOREIGN_FUNCTION_WORDS` is evidence-driven, not exhaustive.** It covers the
   six languages seen in the 2026-07-20 shortlist plus their high-volume GDELT
   neighbours, with every English-colliding word deliberately excluded ("mit"/MIT,
   "dem"/Dem., "von"/von Neumann, "des"/Des Moines, "los"/"las"/Los Angeles, "die",
   "war", "duke", "van"). A Latin-script language outside the list (Czech, Swedish,
   Vietnamese-without-diacritics…) can still slip through on a title with no
   diacritics. The `gdelt_non_english_candidates_rejected` reason mix plus the
   shortlist is how the next entry gets evidence.

6. **Entity-encoded titles are still shipped verbatim downstream.** GDELT delivers
   `&#x27;` / `&#xFC;` in `<PAGE_TITLE>` and nothing in `agents/` unescapes them —
   the 2026-07-20 shortlist headlines show the raw entities. This gate decodes for
   the language DECISION only and deliberately does not mutate the title (Rule 3,
   out of scope). Headline hygiene is a separate concern
   (`agents/shared/headline_quality.py`) and worth a slice.

## Secondary concern from the issue (disposition)

The root `environment` interest's broad anchors admitted **"Fish And Chips Spots
Worth Knowing About At The Jersey Shore"** (1057thehawk.com) and the **RTX hot-spot**
story (purepc.pl) — both stamped `matched_interest_slugs: ["environment"]`. Not
folded in: it is `interest_search_query` term hygiene, which is exactly the scope of
open issue **#69** (catalog backfill to the comma-anchor contract + no-comma hygiene
guard). No dup filed; the evidence was commented onto #69 instead. Note the RTX row
is now rejected by THIS gate anyway (Polish), so #69 inherits the Fish-and-Chips case
as its concrete acceptance fixture.
