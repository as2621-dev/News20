# Archetype Profiles — News20 Source Recommendation

**Status:** 🟡 DRAFT — the exact set + vectors are open question #1 in `plans/m5-m6-personalization-sources-control-surface.md`; **lock via `/cmo`** before seeding the catalog (Phase 5B).
**Purpose:** Pre-defined named profiles that turn a user's interest-picker selections into **instant** source recommendations, per `personalization-and-source-curation-spec.md` §2.2–§3.

---

## 1. How archetypes work

1. The recursive interest picker (Phase 5A) outputs the user's explicit follows (topics + entities), which roll up into a **category vector** over the 8 top-level categories.
2. At onboarding completion, the user is mapped to the **nearest archetype** by similarity (cosine/Jaccard) between their category vector and each archetype's vector.
3. Each archetype carries **pre-computed source lists** (YouTube channels / podcasts / X handles / personalities), so the three source-recommendation screens render instantly instead of computing per-user.
4. Sub-niche picks **re-rank** the archetype's default lists (e.g. a heavy "AI chips" user gets AI-chips-weighted sources). Re-rank strength = open question #2.

> Archetypes can be added/refined over time without changing onboarding UI (spec §2.2). The M6 research agent (Phase 6A) refreshes each archetype's source lists from community signals.

**Donor mapping:** TL;DW ships **6 personas** — `operator | builder | investor | crypto | macro | creator` (`scripts/seed_catalog/`, `014_catalog_personas.sql`). News20 needs **10–15** spanning the 8 categories. The TL;DW 6 cover Business/Tech/AI; News20 adds Sport, Arts, Politics, Geopolitics, Environment coverage.

---

## 2. The 8-category axis (C1 — pinned)

`AI · Geopolitics · Business · Environment · Politics · Tech · Sport · Arts`

Vectors below are weights 0–3 (0 = none, 3 = primary). Illustrative — **lock real weights in `/cmo`.**

## 3. Draft archetype set (12 — PROPOSAL, not final)

| # | Archetype | AI | Geo | Bus | Env | Pol | Tech | Spt | Art | Notes / donor persona |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| 1 | **AI & Frontier Tech** | 3 | 0 | 1 | 0 | 0 | 3 | 0 | 0 | TLDW `builder` |
| 2 | **Markets & Macro** | 0 | 1 | 3 | 0 | 1 | 0 | 0 | 0 | TLDW `investor`+`macro` |
| 3 | **Startup Operator** | 1 | 0 | 3 | 0 | 0 | 2 | 0 | 0 | TLDW `operator` |
| 4 | **Crypto & Fintech** | 0 | 0 | 2 | 0 | 0 | 2 | 0 | 0 | TLDW `crypto` |
| 5 | **Geopolitics & World** | 1 | 3 | 1 | 0 | 2 | 0 | 0 | 0 | NEW |
| 6 | **US Politics & Policy** | 0 | 1 | 0 | 0 | 3 | 0 | 0 | 0 | NEW |
| 7 | **Climate & Energy** | 0 | 1 | 1 | 3 | 1 | 1 | 0 | 0 | NEW |
| 8 | **Sports Fan** | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | NEW |
| 9 | **Arts & Culture** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | NEW (TLDW `creator`-adjacent) |
| 10 | **Creator / Media** | 1 | 0 | 1 | 0 | 0 | 2 | 0 | 2 | TLDW `creator` |
| 11 | **Tech Generalist** | 2 | 1 | 1 | 1 | 0 | 3 | 0 | 1 | NEW |
| 12 | **Balanced Generalist** | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | default / fallback |

> A user who matches no archetype well (low similarity to all) falls back to **#12 Balanced Generalist**.

> **Seeded by Phase 5B (SP2):** this draft 12 set is the one seeded into the `archetypes` table by `supabase/seed/archetypes.sql`. Each row's 0–3 weights above are normalized (÷ row sum, 4 dp) into `archetype_vector` over the 8 lowercase keys `ai, geopolitics, business, environment, politics, tech, sport, arts`. Slugs (kebab-case): `ai-frontier-tech`, `markets-macro`, `startup-operator`, `crypto-fintech`, `geopolitics-world`, `us-politics-policy`, `climate-energy`, `sports-fan`, `arts-culture`, `creator-media`, `tech-generalist`, `balanced-generalist`. Re-seedable in place (upsert on `archetype_slug`) once `/cmo` locks the final set — no schema change.

---

## 4. Per-archetype source list schema

Each archetype maps to curated source lists, seeded via the TL;DW `scripts/seed_catalog/` flow (`data/{type}.{archetype}.json`, file position = popularity rank). Stored against `sources.personas text[]` / `personalities` (`014_catalog_personas.sql`).

```jsonc
// reference shape — one file per (type, archetype), e.g. channels.ai-frontier-tech.json
{
  "archetype": "ai-frontier-tech",
  "youtube_channels": [ { "name": "...", "handle": "@...", "rank": 1 } ],
  "podcasts":         [ { "name": "...", "itunes_id": "...", "rank": 1 } ],
  "x_handles":        [ { "name": "...", "handle": "@...", "rank": 1 } ],   // NEW — no donor resolver
  "personalities":    [ { "name": "...", "rank": 1 } ]
}
```

Resolution at seed time: channels via YouTube `channels.list?forHandle`, podcasts via iTunes, personalities via Wikipedia photo lookup (all in `scripts/seed_catalog/`). **X handles have no donor resolver — build in Phase 5C.**

---

## 5. Open questions (→ `/cmo`)
1. **The real set & count** (10–15) and their exact 8-category vectors. This draft is 12.
2. **Re-rank strength** — how hard sub-niche picks re-weight an archetype's defaults.
3. **Multi-archetype users** — map to one nearest, or blend top-2 (TL;DW round-robin merges multiple — `api/sources/recommended/route.ts:148-189`)?
4. **Curation source** — hand-seed v1, then let the M6 research agent (Phase 6A) refresh.
