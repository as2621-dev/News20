# Phase 2 (M2 Detail + Trust) — Sub-phase 1 execution report

**Sub-phase:** Detail data layer + types
**Status:** SUCCESS
**Date:** 2026-05-31

## What was built
A typed Supabase-direct Detail data layer for the swipe-right Story Detail view.

- `src/types/detail.ts` — the `StoryDetail` contract + its member interfaces, each mapped 1:1 to a Postgres table per `reference/supabase-schema.md`.
- `src/lib/detail/fetchStoryDetail.ts` — `fetchStoryDetail(story_id, client?)` issuing six concurrent (`Promise.all`) read-only PostgREST queries and returning one populated, validated `StoryDetail`.
- `tests/lib/detail/fetchStoryDetail.test.ts` — 7 unit tests mocking the Supabase client at the boundary (matches `tests/lib/feed/supabaseFeed.test.ts` style).

## Files touched (only these three)
- `src/types/detail.ts` (new)
- `src/lib/detail/fetchStoryDetail.ts` (new)
- `tests/lib/detail/fetchStoryDetail.test.ts` (new)

No other file edited. `feed.ts`, components, plans, references, migrations, seed all untouched.

## Schema column → field mapping (verified against supabase-schema.md AND the live seed)
- `detail_chunks.chunk_index, chunk_text` → `DetailChunk` (ordered `.order("chunk_index", asc)`), keyed on `detail_story_id`.
- `story_trust.coverage_left_count / coverage_center_count / coverage_right_count / coverage_outlet_count / blindspot_lean / opposing_view_text` → `TrustSummary`, keyed on `trust_story_id` (1:1, `.maybeSingle()`).
- `story_timeline.timeline_event_index, timeline_when_label, timeline_what_text` → `TimelineEvent` (ordered `.order("timeline_event_index", asc)`), keyed on `timeline_story_id`.
- `story_sources.source_outlet_name, source_bias_lean, source_article_url, source_published_utc, source_is_citation` → `StorySource`, keyed on `source_story_id`.
- `suggested_questions.question_index, question_text` → `SuggestedQuestion` (ordered `.order("question_index", asc)`), keyed on `question_story_id`.
- `stories.story_key_figure_value, story_key_figure_label` → `KeyFigure`, keyed on `story_id` (`.maybeSingle()`).

Each detail table uses a DIFFERENT story-FK column name; the test asserts every `.eq(...)` keys on the correct one (a copy-paste of the wrong column would silently return another story's rows — Rule 9).

## OQ#2 handling (the `DetailVisual[]` conflict, Rule 7)
Resolved toward the schema (more recent, more tested): **no `detail_visuals` / `DetailVisual[]` gallery** in either the type or the fetch. The Detail's only visuals are the key-figure card (`KeyFigure`) and the ambient poster (already on the reel `Story`). Both `detail.ts` and `fetchStoryDetail.ts` document this in their header JSDoc. The stale `api-contracts.md` field names were used only where they agree with the schema (e.g. `StorySource.source_outlet_name`); the visuals gallery was dropped. (Action item from the phase — marking `DetailVisual[]` stale in `api-contracts.md` itself — was NOT done here: editing reference docs is outside this SP's scope lock.)

## Divergences from the api-contracts shape (documented, not drift)
- `StoryDetail` carries `trust_summary`, `key_figure`, `timeline`, and `suggested_questions` in addition to `detail_chunks` + `sources`, because the schema normalizes those into their own tables and the Detail view (SP2–SP4) renders all of them. `readable_text_chunks: string[]` from the contract became `detail_chunks: DetailChunk[]` (objects carry `chunk_index` so ordering is assertable and SP2 can key on it).
- `BiasBreakdown` (pct triple in the contract) is represented as raw `coverage_*_count` integers per the schema; SP3 computes proportions from the counts (the phase explicitly puts the proportion math in SP3).

## Self code-review findings + fixes
- (info) `.maybeSingle()` chains were collapsed to one line by Biome's formatter — accepted, formatter-driven, test fake supports the chain shape. No behavior change.
- (low→fixed) Nullability: `blindspot_lean`, `opposing_view_text`, `source_bias_lean`, `source_article_url`, `source_published_utc`, and both key-figure fields are `| null` in the types and passed through verbatim — no coercion to a default lean (which would fabricate a blindspot). Covered by the explicit null-blindspot test.
- (med→addressed) Missing-data loudness: missing `story_trust` (1:1) and a not-found `story_id` each throw a `fix_suggestion` error rather than returning a half-blank object (Rule 12 fail-loud). Tested.
- No secret/token logging anywhere; only `error.message` is surfaced, matching `supabaseFeed.ts`.

## Validation results
- `npx tsc --noEmit` → exit 0 (clean).
- `npx biome check` on all three files → "No fixes applied" (clean) after one autofix pass.
- New test file: **7/7 passed**.
- Full suite `vitest run` → **12 files / 102 tests passed** (11 pre-existing files green; no regressions).

## Live smoke (attempted, PASSED)
`fetchStoryDetail("s1")` run via `tsx --env-file=.env` against the seeded project (read-only):
- 3 detail chunks, indices `[0,1,2]` (in order)
- coverage left/center/right = 9/7/3, `coverage_outlet_count` = 19
- `blindspot_lean` = "right", opposing-view present
- key figure = `~20%` / "of global oil transits Hormuz"
- 3 timeline events, indices `[0,1,2]` (in order)
- 2 sources, 3 suggested questions

Confirms the real seeded shape matches the contract.

## DoD check — PASS
- Unit test mocks the Supabase client and asserts each column maps to the right field; `detail_chunks` returned in `chunk_index` order and `story_timeline` in `timeline_event_index` order, with `.order(...)` calls asserted (fails if a column is mis-mapped or an ordering is dropped — Rule 9). ✅
- Returned object supports ≥1 ordered chunk, coverage counts + `coverage_outlet_count`, ≥1 ordered timeline event, key-figure value/label, sources, suggested questions. ✅ (verified by both the mocked test and the live smoke)
- Biome clean; tsc 0. ✅

## Concerns / handoff for SP2
- **The shape SP2 consumes** is the exported `StoryDetail` (below). SP2 renders `detail_chunks` (Playfair body, already in `chunk_index` order — do not re-sort), `key_figure` into `KeyFigureCard`, and passes `trust_summary` + `timeline` down to the `<TrustStrip/>` (SP3) and `<StoryTimelineDrawer/>` (SP4) stubs.
- **The `story_id` arg:** SP2 must call `fetchStoryDetail(activeStory.digest_id)` — the reel `Story.digest_id` field (`src/types/feed.ts`) holds the `stories.story_id` slug (`"s1"`..`"s5"`), per the pre-existing naming quirk. Do not rename `feed.ts`.
- `key_figure.key_figure_value` / `key_figure_label` are nullable — SP2 should render the key-figure card conditionally (a story may have no key figure).
- `trust_summary.blindspot_lean` and `opposing_view_text` are nullable — SP3 shows the blindspot chip / opposing-view card only when non-null.
- The action to mark `DetailVisual[]` stale in `api-contracts.md` was left for the doc owner (out of this SP's file scope).

### Exported `StoryDetail` shape
```ts
interface StoryDetail {
  story_id: string;                       // the stories.story_id slug ("s1".."s5")
  detail_chunks: DetailChunk[];           // { chunk_index, chunk_text }, ordered by chunk_index
  trust_summary: TrustSummary;            // coverage_{left,center,right}_count, coverage_outlet_count,
                                          //   blindspot_lean: BiasLean | null, opposing_view_text: string | null
  key_figure: KeyFigure;                  // { key_figure_value: string|null, key_figure_label: string|null }
  sources: StorySource[];                 // { source_outlet_name, source_bias_lean|null, source_article_url|null,
                                          //   source_published_utc|null, source_is_citation }
  timeline: TimelineEvent[];              // { timeline_event_index, timeline_when_label, timeline_what_text },
                                          //   ordered by timeline_event_index
  suggested_questions: SuggestedQuestion[]; // { question_index, question_text }, ordered by question_index
}
type BiasLean = "left" | "center" | "right";
```
