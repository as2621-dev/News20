# Phase 5b · Sub-phase 2 — Archetype definitions + vectors seed

**Status:** SUCCESS
**Sub-agent:** fresh-context, SP2 only. No commit (orchestrator commits at phase end).

## What shipped
The draft 12-archetype set from `reference/archetypes.md` §3 seeded into the `archetypes` table (created by migration `0009_content_sources.sql`), each as a normalized weight vector over the 8 pinned categories, plus in-SQL apply-time assertions and a reference-doc note marking this set as the one seeded by Phase 5B.

## Files touched (only these)
- `supabase/seed/archetypes.sql` — **created**. Idempotent upsert of 12 archetypes + 3 `DO $$` assertion blocks.
- `reference/archetypes.md` — **edited** (surgical). One blockquote after the §3 fallback note recording the seeded set + kebab slug list + re-seedability.
- `.agents/execution-reports/phase-5b-source-data-model-catalog-sub-2.md` — this report.

(Nothing else touched. The untracked `0009`, `src/types/source.ts`, sub-1 report and progress files belong to sibling sub-phases.)

## Divergences from the brief
1. **Migration number:** brief said `0008`; the schema actually lives in `0009_content_sources.sql` (0007/0008 taken by sibling phases). The seed targets the `archetypes` table by name, so the migration number is immaterial to the seed — column names (`archetype_slug`, `archetype_label`, `archetype_vector jsonb`) match 0009 exactly.
2. **Conflict handling — `do update`, not `do nothing`:** the existing seed files (`interests.sql`, `outlets.sql`) use `on conflict … do nothing`. This seed DIVERGES to `on conflict (archetype_slug) do update set label/vector` because the brief + 0009 both require the draft set to be **re-seedable in place without schema change** — append-only `do nothing` could not overwrite a tuned vector. Flagged inline in the SQL header (Rule 7: the more-recent/explicit requirement wins). This is the right call and is noted, not silent.
3. **Normalization rounding:** used **natural 4-decimal rounding** (weight ÷ row sum, round 4 dp) with NO max-element fix-up. This keeps symmetric raw weights symmetric (e.g. `ai-frontier-tech`: ai=tech=0.4286) and leaves residuals ≤ 0.0002 — inside the ±0.001 assertion tolerance. A fix-up-the-largest approach was rejected because it broke the ai=tech symmetry and would skew the cosine probe.

## Self-review findings + fixes
- **CRITICAL (found + fixed):** initial draft left a stray `function cosine_unused() returns void …` nested inside the DECLARE block of assertion (c) — invalid PL/pgSQL (cannot declare a function inside a DO block). Removed; the cosine is computed inline in the `ORDER BY` instead. Fixed before validation.
- Verified: 12 rows; all slugs unique + kebab-case; every vector carries all 8 keys; column names match 0009; upsert is idempotent (converges on re-run); `^` (numeric exponentiation), `sqrt(numeric)`, `jsonb ? key`, `->> ` casts, `is distinct from`, and `unnest(arr) as k` correlated subqueries are all valid PostgreSQL.

## Normalization arithmetic (weight ÷ row sum, 4 dp)

Keys order: `ai · geopolitics · business · environment · politics · tech · sport · arts`

| Archetype (slug) | raw weights | Σraw | normalized vector (nonzero) | Σnorm |
|---|---|:--:|---|:--:|
| ai-frontier-tech | 3,0,1,0,0,3,0,0 | 7 | ai .4286, business .1429, tech .4286 | 1.0001 |
| markets-macro | 0,1,3,0,1,0,0,0 | 5 | geo .2, business .6, politics .2 | 1.0 |
| startup-operator | 1,0,3,0,0,2,0,0 | 6 | ai .1667, business .5, tech .3333 | 1.0 |
| crypto-fintech | 0,0,2,0,0,2,0,0 | 4 | business .5, tech .5 | 1.0 |
| geopolitics-world | 1,3,1,0,2,0,0,0 | 7 | ai .1429, geo .4286, business .1429, politics .2857 | 1.0001 |
| us-politics-policy | 0,1,0,0,3,0,0,0 | 4 | geo .25, politics .75 | 1.0 |
| climate-energy | 0,1,1,3,1,1,0,0 | 7 | geo .1429, business .1429, env .4286, politics .1429, tech .1429 | 1.0002 |
| sports-fan | 0,0,0,0,0,0,3,0 | 3 | sport 1.0 | 1.0 |
| arts-culture | 0,0,0,0,0,0,0,3 | 3 | arts 1.0 | 1.0 |
| creator-media | 1,0,1,0,0,2,0,2 | 6 | ai .1667, business .1667, tech .3333, arts .3333 | 1.0 |
| tech-generalist | 2,1,1,1,0,3,0,1 | 9 | ai .2222, geo .1111, business .1111, env .1111, tech .3333, arts .1111 | 0.9999 |
| balanced-generalist | 1,1,1,1,1,1,1,1 | 8 | all eight = .125 | 1.0 |

Max abs residual from 1.0 = **0.0002** (climate-energy) — within the ±0.001 assertion tolerance.

## Validation
- **No DB to apply against** — no `psql`/`postgres` binary, no `supabase/config.toml`. The 3 in-SQL assertions are **DEFERRED-offline** (written to fail loud on manual apply; NOT executed here — Rule 12).
- **Hand-recomputed normalization:** all 12 vectors re-derived independently in Python and parsed straight out of the SQL file — every embedded JSON literal is valid JSON, carries all 8 keys, and matches the expected normalized values exactly; every sum is within ±0.0002. PASS.
- **Cosine probes (simulated offline, identical formula to the SQL):** heavy ai+tech probe → nearest `ai-frontier-tech` (cos 0.9733, runner-up tech-generalist 0.8575); flat probe → `balanced-generalist` (cos 1.0). Matches the DoD expectation. PASS (offline); the in-SQL version is DEFERRED-offline.
- **`npm run lint` (biome):** clean — "Checked 106 files … No fixes applied." SQL/MD are out of biome's scope; the JS/TS surface is undisturbed. PASS.
- **SQL syntactic review:** well-formed. Stray nested-function bug found and fixed pre-validation.

## Definition of done (Sub-phase 2)
> "12 archetype rows seeded; every `archetype_vector` covers all 8 categories and is normalized; a cosine-similarity query of a sample interest vector (e.g. heavy ai+tech) returns ai-frontier-tech as nearest and balanced-generalist for a flat vector. SQL/unit assertion."

| DoD item | Result |
|---|---|
| 12 archetype rows seeded | **PASS** (static: 12 VALUES rows, unique kebab slugs; assertion (a) re-checks at apply) |
| every vector covers all 8 categories | **PASS** (static: each JSON literal has all 8 keys; assertion (b) re-checks) |
| every vector normalized (Σ≈1.0) | **PASS** (static: hand-verified Σ within ±0.0002; assertion (b) enforces ±0.001) |
| cosine: ai+tek → ai-frontier-tech, flat → balanced-generalist | **DEFERRED-offline** for the in-SQL query (no DB). Offline simulation with the identical formula PASSES. |
| SQL/unit assertion present | **PASS** (3 `DO $$` blocks: count, all-keys+sum, cosine). Execution DEFERRED-offline. |

## Concerns / flags
- **Rule 9 (tests encode intent):** I did **not** add a separate offline code test, per the brief's instruction. The intent ("12 normalized 8-key vectors; ai+tech→ai-frontier-tech; flat→balanced-generalist") is encoded in the 3 in-SQL assertions, which fail loud on apply. A standalone code test would have to either spin up a DB or duplicate the 12 vectors in a second source of truth (drift risk). I judge the in-SQL assertions sufficient and am **flagging** the choice rather than silently adding a file — as the brief asked.
- **Assertion (b) tolerance is ±0.001**, deliberately wider than the ≤0.0002 actual residual, to leave headroom if `/cmo` later swaps in vectors with different denominators. Acceptable; documented inline.
- **Cosine assertion robustness:** the ai+tech probe's margin over the runner-up is comfortable (0.9733 vs 0.8575). If `/cmo` re-weights archetypes, re-confirm the probe still resolves uniquely before relying on assertion (c).
