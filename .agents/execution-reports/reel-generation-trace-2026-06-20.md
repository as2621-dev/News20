# Reel-generation trace — ash@gmail.com, feed_date 2026-06-20

Source logs: `/tmp/m3b_produce2.log` (topic run), `/tmp/src_produce.log` (source run), `/tmp/poster_fill.log` (posters).
Profile: `ash@gmail.com` (b316800d). 26 followed interests, allocation = ai5/tech2/business6/geopolitics4/sport5/arts4/youtube3/x1 (=30 slots).

---

## A. Topic run (`run_live_batch.py`, BigQuery ingest, ~35 min wall)

Stage-by-stage funnel (each number is a logged event count):

| # | Stage | In → Out | Event / counter |
|---|---|---|---|
| 1 | **Interest ingest** (GDELT BigQuery, 26 interests) | → **1950** raw candidates | `interest_keyed_ingestion_completed.total_candidates=1950`, `failed_interests=0` |
| 2 | **Canonicalize / cluster** (URL + 0.85-title dedup) | 1950 → **1128** | `canonical_stories=1128` (`reused_existing_story_ids=0`) |
| 3 | **Body extraction** (per candidate) | — | `gdelt_extract_body_success=1071`, `gdelt_extract_body_failed=50` |
| 4 | **Produce gate** (servable / not-yet-produced) | 1128 → **1128** | `produce_gate_batch_completed.produced=1128, skipped=0` |
| 5 | **Editorial LLM dedup** (1 Gemini call, near-angle dups) | 1128 → **808** | `produce_dedup_completed.kept=808, dropped=320, duplicate_group_count=57` |
| 6 | **Per-category caps** (ash's Build-your-30 budgets) | 808 → **28** | `produce_caps_applied.kept=28, dropped=780`; per-cat `{business6, ai5, sport5, geopolitics4, arts4, tech4}` |
| 7 | **Write/produce phase** (per reel) | 28 → **17** | see drop breakdown ↓ |
| 8 | **Persist** (audio + analytics) | 17 → **17** | `persist_digest_completed=17`, `gemini_tts_render_completed=17` |
| 9 | **Feed assembly** | → **17** interest rows | ash feed; `feeds_written=4` (all active users) |

### The write-phase drop (stage 7): 28 → 17 (the eval-critical one)
Accounting is exact: **28 = 17 published + 3 editorial-fail + 8 verification-halt**

- **3 dropped — editorial rewrite JSON parse failure.** `editorial_rewrite_failed` ×3, `error_type=PipelineStageError "Failed to parse LLM response as JSON after all extraction strategies"`. (Fallback keeps original text, but these did not reach a published reel.) → **11% editorial JSON-failure rate** (3/28).
- **8 dropped — verification halt (anti-hallucination gate).** `verification_stage_completed`: 25 reached verification, **17 `is_grounded=true`, 8 `is_grounded=false`**. Each ungrounded one → `verification_halt_triggered` → `write_phase_verification_halt` ("Digest makes claims the single source does not support; not published"). → **68% grounding pass rate** (17/25); unsupported-claim counts ranged 1–3 per halted digest.
- **17 published** — grounded, scripted, TTS-rendered, enriched (`detail_enrichment_completed=17`, `coverage_report_completed=17`), persisted.

Per-reel LLM calls in the write phase: `llm_call_started/completed = 85` (≈ scripting + verification + editorial across the cohort). `orchestrator_poster_skipped=17` (POSTER_MODE=batch → posters deferred).

---

## B. Source run (`produce_source_reels.py`, FORCE_REINGEST=1)

| Stage | In → Out | Counter |
|---|---|---|
| Poll followed sources | 10 polled | `polled_sources=10, skipped_not_due=0, failed_sources=0` |
| Fetch items | → **29** fetched | YouTube: BBC=15, CNBC=3, Economist=3, TwoMinutePapers=1, Veritasium=1, Lex/Kurzgesagt/others=0. X: TechCrunch=5, elonmusk=5 |
| Not-substantive drop | 29 → 24 | `items_dropped_not_substantive=5` |
| Cluster / promote | → **24** stories | pool = **19 YouTube + 5 X** |
| Produce (cap MAX_YT=3, MAX_X=1) | 24 → **4** | `produced 3 youtube + 1 x reels` (2 verification halts during this run too) |
| Feed rebuild | → **21 rows** | `{interest:17, source:4}`, source at positions 18–21; prior feed backed up to JSON |

**X caveat:** all tweet screenshots failed to render (`x_account_screenshot_missing`) → the X reel uses a Nano-Banana **synthetic** poster, not a tweet image. YouTube reels use channel thumbnails.

---

## C. Poster fill (`fill_batch_posters.py`, Nano Banana Pro = `gemini-3-pro-image-preview`)

`reels needing poster = 17 → posters attached = 17, prep-failed = 0, still posterless = 0`. (Source reels already had posters → skipped.) All graded to 1080×1920 webp, accent `#22D3EE`.

---

## D. Final state (verified against DB)

**21/21 reels with poster + audio.** 17 interest + 4 source (3 YT + 1 X). Feed totals 21, not the 30 budget — the gap is the stage-7 attrition above (3 editorial + 8 verification + 9 capacity, since caps only kept 28 vs the 30 budget and some categories had no surviving candidates).

---

## E. Suggested eval targets (highest-signal first)

1. **Verification grounding pass-rate** — 17/25 = **68%** grounded. This is the single biggest yield lever. Eval: does the scripting prompt over-claim vs a *single* source? (Multi-source stories would raise grounding.) Track `unsupported_count` distribution.
2. **Editorial-rewrite JSON validity** — 3/28 = **11%** hard-fail on JSON parse. Eval: structured-output reliability of the editorial model; candidate for schema-constrained decoding.
3. **Ingest→canonical dedup ratio** — 1950→1128 (**42%** collapse) and editorial dedup 1128→808 (**28%**). Eval: are the two dedup passes (URL/title vs LLM near-angle) double-counting or complementary? (This is exactly what M3b's clustering engine will replace/measure.)
4. **Cap starvation** — 808→28 means category budgets, not supply, bound the feed; but final 17 < 28 cap means *quality gates*, not caps, are the true bottleneck this run.
5. **Source yield** — 4/10 sources produced (BBC dominant; Lex/Kurzgesagt/Veritasium returned 0 fresh captioned uploads). X screenshot render = 0% success → poster fidelity eval.
