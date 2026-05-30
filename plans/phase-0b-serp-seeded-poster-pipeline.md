# Phase 0b: SERP-Seeded Poster Pipeline — search → score → seed → generate

**Milestone:** M0 — Quality spike (de-risk gate). Reworks the *poster image* step only.
**Status:** Not started (plan)
**Estimated effort:** L
**Supersedes:** the concept-first `agents/m0/{poster_prompts,generate_posters}.py` path for the 5 M0 posters (kept as a fallback; see §Cleanup). `generate_variants.py` was a throwaway A/B/C register experiment.

---

## Goal

For each of the 5 M0 stories, **find real reference imagery from a Google-Images SERP search of the headline, score 5 candidates against a weighted rubric, pick the winner, and use it as the seed** (image + text) for a Nano Banana Pro generation that recasts it into a brand-graded, metaphor-forward, 9:16 textless poster. The output reads as "from this app" via the locked house grade + single segment accent.

**Why this over the current pipeline:** concept-first prompts (current) produce clean but *generic* metaphors with no grounding in what the story actually looks like. Seeding from a real top-ranked news image gives the generator true subject reference (the actual person/event/object), then we transform it — "a little metaphor, a little graphical creativity," not a copy.

**Success criteria (loop until all true):**
1. `python -m agents.m0.run_poster_pipeline` produces 5 posters at `assets/m0/digest-{1..5}/poster.png`, each 9:16, brand-graded, textless.
2. Each story emits a `selection-report.json`: the refined query, the ≤5 candidates with their 6 criterion scores + weighted totals, the winner, and the synthesized prompt.
3. Every external call (Serper, scoring LLM, prompt-synthesis LLM, Gemini image) is mocked in a unit test that fails on wrong business logic (weighted-score math, winner selection, query construction, reference-image passed to the generator).
4. Ruff clean; no key value ever logged; `SERPER_API_KEY` documented in `.env.example`.

---

## Pipeline flow (one story)

```
headline + summary
   │
   ▼  (1) image_query_builder  — LLM: headline → smart Google-Images query (judgment, Rule 5)
refined_query
   │
   ▼  (2) serper_image_search  — POST google.serper.dev/images  (num=10)
~10 raw image results
   │
   ▼  (3) gates (pass/fail, NOT scored)  — dedup by URL · min shortest-side ≥320px · safe-search · keep best 5
≤5 candidates
   │
   ▼  (4) download_candidates  — fetch bytes → assets/m0/<digest>/refs/cand-{n}.<ext>
≤5 downloaded images
   │
   ▼  (5) image_scorer  — multimodal LLM: each image + headline → 6 criterion scores (0–10) → weighted total
5 ScoredCandidate
   │
   ▼  (6) select  — highest weighted total wins (tie-break: relevance, then iconic)
winner (+ scores saved to selection-report.json)
   │
   ▼  (7) reference_prompt_synthesizer  — LLM: winner image + headline + house rules + accent → NEW textless poster prompt (recast, not copy)
synthesized_prompt
   │
   ▼  (8) generate_posters (adapted)  — Nano Banana Pro: [reference image Part + synthesized prompt] → image-conditioned render
raw poster bytes
   │
   ▼  (9) grade_and_brand  — deterministic Pillow pass: duotone toward #020617 + single accent + grain + vignette + lower-40% scrim (poster-pipeline §6/§7)
assets/m0/<digest>/poster.png  +  selection-report.json
```

Steps 1, 5, 7 are the only LLM/judgment calls (Rule 5). Steps 2, 3, 4, 9 are deterministic code.

---

## The scoring rubric (locked by user, 2026-05-29)

Each candidate is scored **0–10 on all six**, then weighted. The image is a **seed**, so criteria measure what the seed uniquely supplies — not things the generator imposes anyway (orientation, accent, grade) are de-weighted or moved to gates.

| # | Criterion | Weight | What the scorer judges |
|---|---|---|---|
| 1 | **Headline aptness** | **×2** | Does the image actually depict THIS story's subject/event/person? Wrong subject = unusable however pretty. |
| 2 | **Metaphor / transformation potential** | **×1.5** | If recast, how likely to become a strong conceptual/pun poster (not just a restyle)? The "graphical creativity" lever. |
| 3 | **9:16 vertical fit** | **×1** | Does the focal arrangement survive a vertical recompose? (Soft — generator recomposes; also a gate against pure panoramas.) |
| 4 | **Single dominant + recognizable subject** | **×1** | One clear, identifiable hero (person/object), not a cluttered crowd/podium. Maps §1 "one idea, one object." |
| 5 | **Iconic distinctiveness** | **×1** | Striking & memorable vs. a generic forgettable wire/stock photo. |
| 6 | **Emotional tone match** | **×3** | Does the image's mood (tension / awe / triumph / grief) match the story's valence? (User-weighted highest.) |

**Weighted total** = `2·c1 + 1.5·c2 + 1·c3 + 1·c4 + 1·c5 + 3·c6`  → max **95**. Report both raw per-criterion and the total.

**⚠ Surfaced tradeoff (Rule 7):** emotional (×3) outweighs relevance (×2), so a striking-but-slightly-off image can win. Mitigation baked in: **relevance is also a soft gate** — any candidate scoring `c1 < 5` is disqualified before ranking, so the winner is always genuinely on-topic. Weights live in one constant (`SELECTION_CRITERIA`) and are trivially tunable.

**Gates (pass/fail, run BEFORE scoring — cheap, don't waste a multimodal call):** dedup by full URL · shortest side ≥ 320px · drop obvious watermarked-stock domains (gettyimages/shutterstock/alamy/istockphoto preview thumbs) · Serper safe-search on. Fetch `num=10`, gate down, score the top 5.

---

## Reuse map (ported from Canvas — read before porting, Rule 8)

Canvas implements this in **TypeScript**; News20's poster pipeline is **Python**, so we **PATTERN-port** (copy the contract, rewrite the body in Python/httpx). Canonical Canvas source: `/Users/asheshsrivastava/Canvas/creator-studio/src/lib/services/search-service.ts`.

| Canvas (TS) | News20 (Python) | Decision |
|---|---|---|
| `search-service.ts` → Serper `/images` call | `agents/m0/serper_image_search.py` | **PATTERN** — same endpoint/headers/body, httpx async |
| `SerperImageItem` / `SerperImagesResponse` types | Pydantic models in `agents/m0/poster_models.py` | **PORT** (typed boundary) |
| `deduplicateByUrl()` + `filterSmallImages()` (<200px) | gate functions in `serper_image_search.py` (raise floor to 320px) | **ADAPT** |
| `search-cache.ts` (5-min LRU) | **SKIP for M0** — 5 one-shot stories, no caching needed (Rule 2) | SKIP |
| Firecrawl agentic mode | **SKIP** — Serper Google Images only for M0 | SKIP |

**Exact Serper contract (verbatim from Canvas, to replicate):**
- `POST https://google.serper.dev/images` · header `X-API-KEY: {SERPER_API_KEY}` · body `{ "q": <query>, "num": 10, "page": 1 }`
- Response: `{ images: [{ title, link, thumbnailUrl, imageUrl, imageWidth, imageHeight }] }`
- Map → `ImageCandidate{ candidate_id, title, source_page_url(link), thumbnail_url, full_image_url(imageUrl), width_px, height_px }`
- ⚠ **Security:** the Canvas `.env` has a **live Serper key committed in plaintext** — do NOT copy that value; News20 sets its own `SERPER_API_KEY`, and the Canvas one should be rotated (flagged, Canvas's concern).

---

## Sub-phases (exactly 4; disjoint files for parallel `/run-phase`)

### Sub-phase 1 — Serper image client + smart query builder
- **Files touched:** `agents/m0/serper_image_search.py` (NEW — async httpx POST, dedup + min-resolution gate, returns ≤N typed `ImageCandidate`), `agents/m0/image_query_builder.py` (NEW — LLM turns headline+summary → one optimized Google-Images query; judgment, Rule 5), `agents/m0/poster_models.py` (NEW — `ImageCandidate` + shared Pydantic models), `agents/shared/settings.py` (ADD `serper_api_key: SecretStr`), `.env.example` (+ `SERPER_API_KEY`), `requirements.txt` (+ `httpx`), `tests/agents/m0/test_serper_image_search.py` + `test_image_query_builder.py` (NEW).
- **What ships:** `search_images(query, num=10) -> list[ImageCandidate]` (gated, deduped) and `build_image_query(headline, summary) -> str`.
- **DoD:** unit test mocks the httpx Serper call and asserts (a) request body `{q,num,page}` + `X-API-KEY` header set, (b) dedup by `full_image_url`, (c) shortest-side <320px dropped, (d) result count capped. Query-builder test mocks the LLM and asserts the refined query is non-empty and derived from the headline (e.g. retains the key entity). Ruff passes; key never logged.
- **Dependencies:** none.

### Sub-phase 2 — Candidate download + 6-criteria multimodal scorer
- **Files touched:** `agents/m0/download_candidates.py` (NEW — fetch bytes for ≤5 candidates → `assets/m0/<digest>/refs/cand-{n}.<ext>`, skip on fetch error/too-small), `agents/m0/image_scorer.py` (NEW — multimodal LLM scores each image vs headline on the 6 criteria; computes weighted total; applies the `c1<5` relevance gate; selects winner), `agents/m0/poster_models.py` (EXTEND — `CriterionScore`, `ScoredCandidate`, `SELECTION_CRITERIA` weights constant, `SelectionReport`), `tests/agents/m0/test_image_scorer.py` (NEW).
- **What ships:** `score_candidates(candidates, headline, summary) -> list[ScoredCandidate]` and `select_winner(scored) -> ScoredCandidate`.
- **DoD:** unit test mocks the multimodal LLM to return fixed per-criterion scores and asserts (a) weighted-total math `2·c1+1.5·c2+c3+c4+c5+3·c6`, (b) `c1<5` disqualification, (c) highest total wins with the documented tie-break (relevance → iconic), (d) image bytes are passed to the LLM as an image part (mocked). Download mocked. Ruff passes.
- **Dependencies:** SP1 (consumes `ImageCandidate`).

### Sub-phase 3 — Reference-seeded prompt synthesis + image-conditioned generation
- **Files touched:** `agents/m0/reference_prompt_synthesizer.py` (NEW — LLM takes the winning image + headline + `poster-pipeline.md` §4/§6 house rules + the segment accent → a NEW textless poster prompt that *recasts* the reference metaphorically, ending in the §9 negative prompt), `agents/m0/generate_posters.py` (ADAPT — `generate_from_reference(client, prompt_text, reference_image_bytes)`: pass the reference image as an input `Part` alongside the text so Nano Banana Pro does image-conditioned generation; keep the existing text-only path as fallback), `agents/m0/poster_prompts.py` (REUSE `HOUSE_GRADE_SUFFIX`), `tests/agents/m0/test_reference_prompt_synthesizer.py` (NEW).
- **What ships:** `synthesize_prompt(winner_image, headline, accent_hex) -> str` and an adapted generator that accepts a reference image.
- **DoD:** unit test mocks the synth LLM + the Gemini client and asserts (a) the synthesized prompt contains the house suffix, the segment accent hex, and the `--no text…` negative, (b) `generate_from_reference` puts BOTH an image part and the text part in `contents`, (c) the reference image is NOT merely echoed (prompt instructs transform/recast — assert key verbs present). Ruff passes.
- **Dependencies:** SP2 (needs the winner image). Confirms Nano Banana Pro multi-input (text+image) support against `google-genai` `types.Part.from_bytes`.

### Sub-phase 4 — Orchestrator + deterministic grade/brand + run the 5 stories
- **Files touched:** `agents/m0/build_poster_from_news.py` (NEW — orchestrates steps 1→9 for ONE story, writes `selection-report.json`), `agents/m0/grade_and_brand.py` (NEW — deterministic Pillow pass: duotone toward `#020617` + single accent, fine grain, soft vignette, lower-40% scrim per §6/§7), `agents/m0/run_poster_pipeline.py` (NEW driver — runs all 5 digests from `digests_input.py`, `--only N` like the current driver, digest-1 first as the probe), `requirements.txt` (+ `Pillow`), `tests/agents/m0/test_build_poster_from_news.py` + `test_grade_and_brand.py` (NEW). Output: `assets/m0/digest-{1..5}/poster.png` + `selection-report.json`.
- **What ships:** 5 final brand-graded posters + 5 selection reports.
- **DoD:** `python -m agents.m0.run_poster_pipeline` produces 5 posters + 5 reports; integration test mocks Serper + both LLMs + Gemini and asserts the full chain emits a poster path + a complete report (query, ≤5 scored candidates, winner, synthesized prompt) per story, and stops early if digest-1 errors (probe). `grade_and_brand` test asserts output is 9:16, the lower-40% band is darkened (scrim), and a brightest-pixel-in-text-band contrast check passes (§7 WCAG gate). `sips`/Pillow confirms 1080×1920 (upscale to target in the grade pass). Ruff passes.
- **Dependencies:** SP1, SP2, SP3.

---

## Phase-level definition of done
Five brand-graded 9:16 posters at `assets/m0/digest-{1..5}/poster.png`, each seeded from a SERP-selected reference and recast by Nano Banana Pro, plus five `selection-report.json` files showing the rubric scores and the winning seed. All SP1–SP4 unit tests pass with every external service mocked; Ruff clean; `SERPER_API_KEY` in `.env.example`; no key value logged. Manual visual smoke: the 5 read on-brief (one idea/object, single accent, ~70% dark, textless) and visibly relate to the real story.

---

## Out of scope
- Caching, Firecrawl agentic multi-source, pagination (Canvas extras — not needed for 5 one-shot stories).
- Wiring this into Trigger.dev / the per-story worker (M1).
- The Remotion render + MP4 (separate, already-built `render_all.py` step — runs after posters exist).
- Replacing the *narration/caption* pipeline (unchanged).
- Reconciling `poster-pipeline.md` §4/§9 (concept-first) with this reference-seeded flow — tracked as an Open Question + a doc update, not code.

---

## Resolved decisions (2026-05-29)
- ✅ **`SERPER_API_KEY`** added to News20 `.env` (verified present, correctly named). SP1 settings field = `serper_api_key: SecretStr`.
- ✅ **All 3 LLM judgment steps use Gemini `gemini-2.5-flash`** (cheap flash, multimodal — verified reachable on the key). One shared constant `GEMINI_LLM_MODEL = "gemini-2.5-flash"` in `poster_models.py`; image gen stays `gemini-3-pro-image-preview`. Single-provider (Gemini only) for the whole pipeline.

## Open questions (decide before / during build)
1. _(resolved — see above)_
2. _(resolved — see above)_
3. **Legal (carry from poster-pipeline §5):** conditioning a generated image on a copyrighted news/wire photo creates a *derivative-work* question for a commercial app. M0 is an internal spike (fine); flag before any ship. Mitigation lever: bias the scorer toward editorial/CC sources, or keep the reference as *text-described inspiration* rather than an image-to-image seed.
4. **Reference strength:** pass the winner as a true image-to-image seed (stronger fidelity, higher IP coupling) vs. text-only description of it (looser, safer). Plan defaults to **image+text input** per the user's "use that image to generate" intent; tunable per story.
5. **Target resolution:** Nano Banana Pro returns ~768×1376 (1K 9:16). The grade pass upscales/letterboxes to 1080×1920; consider `image_config.image_size="2K"` for crisper seeds.

## Cleanup (fold into the phase commit)
- Decide fate of the concept-first path (`poster_prompts.py`, the text-only branch of `generate_posters.py`) — keep as a labeled fallback (recommended) or remove.
- Delete the throwaway `agents/m0/generate_variants.py` (A/B/C register experiment) once the register decision is locked.
- The 5 already-generated posters + 3 digest-1 variants under `assets/m0/` are superseded by this run.

---

## Self-critique

**Product lens:** PASS. Directly serves the M0 gate (better posters → fairer "watch 10 in a row?" test) and the user's explicit redesign. The rubric is the product opinion made executable; emotional-tone ×3 reflects a deliberate "make it feel" bet, with a relevance gate so it can't drift off-topic. No scope creep into M1 (no Trigger/worker/Supabase).

**Engineering lens:** PASS. Reuses the proven Canvas Serper contract (PATTERN-port) and the existing Gemini call path; only new deps are `httpx` + `Pillow` (both standard). Files are disjoint per sub-phase so `/run-phase` can parallelize: SP1 = search/query, SP2 = score, SP3 = synth/generate, SP4 = orchestrate/grade. Each LLM/network boundary is mocked in tests that fail on wrong logic (score math, gate, winner, reference-image wiring), not just on compile. The one shared file `poster_models.py` is additive per sub-phase.

**Risk lens:** PASS with two flags. (1) **Legal** — derivative-work coupling to copyrighted seeds (Open Q3/Q4); acceptable for an internal spike, must resolve before ship. (2) **Determinism** — three LLM steps add variance; mitigated by structured Pydantic outputs, fixed weights, the relevance gate, and a saved `selection-report.json` per story for auditability (Rule 12 — never silently pick). Irreversible steps: none — all output is local files; live cost is bounded (5 stories × [1 Serper + ≤5 scores + 1 synth + 1 image]).
```
