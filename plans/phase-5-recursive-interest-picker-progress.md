# Progress: phase-5-recursive-interest-picker

**Phase file:** plans/phase-5-recursive-interest-picker.md
**Started:** 2026-06-05
**Execution mode:** SEQUENTIAL (strict linear dep chain SP1←SP2←SP3←SP4; SP1 ⚠ irreversible — parallel refused)

## Pre-phase
- [x] Cleared dirty working tree into 3 logical commits (14b923f dedup/sim, c0e881d planning docs, 54f59eb voice orb). Tree clean before SP1.

## Environment facts passed to sub-agents
- Validation: `npm run lint` (biome), `npm test` (vitest run), `npm run build` (next build).
- React component tests: `react-dom/client` + `act` idiom (see tests/lib/detail/trustStrip.test.tsx); **no @testing-library** (scope lock).
- No Docker / local DB / psql / supabase config.toml → SP1 migration runtime-apply DEFERRED to owner manual ops; offline validation only.
- RLS/FK convention from 0005: owner-all `using/with check (col = auth.uid())`, user FK → `auth.users(id)`, FK types must match (`stories.story_id` is text).

## Orchestrator concerns (carry to phase end)
- **Concern #1 (SP4):** SP4 DoD says ranker/feed_assembly reads BOTH follow sources, but SP4 `Files touched` is TS-only (no Python feed-assembly file). Instruct SP4 to persist both + either achieve consumption within file scope or FAIL LOUD (flag Python work as out of declared scope). Do not silently expand scope.
- **Concern #2 (SP3/SP4, from SP1):** registry is 1:1 with prototype PATHS — Nvidia = 3 rows / 3 entity_ids, all NVDA. Dedupe SELECTIONS on canonical identity (kind + ticker, else kind + slug(label)), NOT on entity_id. Persist ONE user_entity_follows row per canonical entity with all paths in follow_path. This is what the SP3 dedupe DoD ("one underlying id with both paths") requires.
- **Concern #3 (SP3/SP4):** picker §8 fonts (Fraunces/Spline Sans/Spline Sans Mono) + tokens aren't in SP3/SP4 file scope (no globals.css / tailwind.config edit allowed). Style self-contained (inline/Tailwind arbitrary values w/ §8 hex); system-font fallback OK for v1 (spec §8 mandates fallbacks). FLAG any needed global font-loading as follow-up — do not silently edit globals.css.

## Sub-phase progress
- [x] 1: Migration 0007 — entity registry + entity follows schema — COMPLETED (DoD PASS offline; runtime-apply DEFERRED to owner). 248 entities, RLS verified, assertion script written.
- [x] 2: Entity registry data layer (listEntities + searchEntities) — COMPLETED (DoD PASS; vitest 230/230, biome clean). search_entities RPC appended to 0007.
- [x] 3: Recursive follow-set engine (FollowSet + FollowChip) — COMPLETED (DoD PASS; vitest 260/260, biome clean, tsc clean for SP3 files). All 4 marquee cases + lazy-mount/preserve + canonical dedupe tested. Added permitted extras: src/lib/pickerSeedTree.ts + 2 test files.
- [x] 4: Picker page + selection tray + follows persistence → ranking — COMPLETED (DoD PASS w/ 1 flagged follow-up; tsc 0, biome clean, vitest 269/269). persistPickerFollows (topics→user_interest_profile, entities→user_entity_follows, custom>seed weight, free-text surfaced, skippable). OnboardingPicker+SelectionTray; InterestChips removed from flow.

## Mid-phase remediation
- SP3 surfaced a real `tsc --noEmit` error in SP2's `src/lib/entities.ts:248` (supabase `.rpc().returns<>()` error-union; biome+vitest don't typecheck). Re-engaged SP2 agent (aabbf57cb247a6283) — it applied the entities.ts boundary cast (justified `// Reason:`, not `any`) but STALLED (watchdog 600s) mid-fix on the test mock. Orchestrator completed the bounded remediation directly: aligned `tests/lib/entities.test.ts::makeRpcClient` to the new direct-`await client.rpc()` shape. RESOLVED — tsc clean (exit 0), biome clean, vitest **260/260**. **Process gap noted: add `npx tsc --noEmit` to the phase-level gate (Step 3a) — vitest+biome alone miss type errors; only next build/tsc catch them.**

## Phase-level gates (after all 4) — ALL RUN ON FULL DIFF
- [x] 3a Phase-level DoD — PASS w/ 2 explicit deferrals (NOT faked, Rule 12): (1) runtime migration apply DEFERRED to owner manual ops (no DB in env; 0007 irreversible); (2) entity-axis ranker consumption = tracked follow-up (owner chose commit-now). Four marquee cases + dedupe + persistence(custom>seed, no-orphan-freetext) + skip all test-verified.
- [x] 3b Slop scan — PASS. No TODO/console/`as any`/dead code/hardcoded hosts/swallowed catches. InterestChips retained = intentional (InterestSelection type dep), not dead code.
- [x] 3c CSO lite — PASS. Auth boundary holds (rows scoped to userId + 0007 owner-all RLS); no injection (RPC parameterized, free-text never written, canonicalization escapes metachars); no secrets; ZERO new deps; logging = labels only, no PII/tokens. `entities` public-read is intentional non-sensitive catalog.
- [x] Single atomic commit

## STATUS: COMPLETE

## Tracked follow-ups (owner-acknowledged)
1. **Runtime apply 0007** — `supabase db push --db-url` (IPv4 session pooler), load `supabase/seed/entities.sql`, run `supabase/tests/0007_entity_registry_assertions.sql` with `ON_ERROR_STOP=1`. Required before live Show-more/Add-your-own + TestFlight.
2. **Entity-axis ranker consumption (FOLLOW-UP SUB-PHASE)** — wire `agents/pipeline/stages/ranking.py` (+ `daily_batch.py`/`interest_keyed_pipeline.py` as needed) to read `user_entity_follows` per user and apply `follow_weight` as an affinity boost (mirror the topic-axis `user_interest_profile` read). Topic axis already consumed; entity axis persisted+ready. Owner chose commit-now (2026-06-05).
3. **Webfonts** — register Fraunces / Spline Sans / Spline Sans Mono (globals.css/tailwind, out of SP3/SP4 scope); picker uses §8 hex tokens + system-font fallback meanwhile (cosmetic).
