> **⚠ SUPERSEDED by phase-5a (2026-06-05) — see `plans/phase-5a-build-your-30-and-entity-ranker.md`.**
> The master-dial + 30-cell-ribbon design below is **retired**. The owner replaced it with the **"Build your 30, in order"** screen: one ordered list of the 8 categories, explicit per-category slot counts, and a manual sequence. The backend contract it writes to (`user_feed_allocation` + the 8 `feed_category` keys, migration 0008) and the allocator that consumes it shipped in phase-5a. The entity-bonus + category-budget allocation are documented in `reference/ranking-spec.md` §3a. This document is kept for design history only.

# Control Surface — Allocation Spec (Settings/Preferences)

**Status:** 🟡 DRAFT scaffold for M5 / Phase 5E. No donor — TL;DW has no allocation UI; build fresh. Re-skin per `reference/design-language.md`.
**Source:** `personalization-and-source-curation-spec.md` §5–§6.
**Goal:** the screen where a user balances the two axes (topics ⨉ sources) across the **fixed 30-story digest window**, with a live preview.

---

## 1. Core principle

Don't make topics and sources fight over one slider. **Separate them, then resolve with one rule:**

> **Pinned sources fill slots first; topics fill the remainder.** (spec §5.5 — kills the overlap problem, no double-counting.)

This feeds the existing per-user `daily_feeds` allocation (`reference/ranking-spec.md`); the control surface sets the *inputs* to that allocation.

---

## 2. Controls (top → bottom)

### 2.1 Master dial — `My Sources ←→ Discovery` (spec §5.1)
A single 0–100 control deciding how many of the 30 slots are reserved for **followed sources** vs **topic discovery**.
- Full left (0) → all 30 are fresh drops from followed channels/handles ("make it all my stuff").
- Full right (100) → pure topic discovery.
- Default ≈ middle.
- **Open Q (spec §7):** hard split, or soft bias when source content is sparse on a given day? See §3.2.

### 2.2 Followed-sources list (spec §5.2)
Each followed source = a row: avatar + name + a **3-state priority**:
`Off · Only their big stuff · Everything they post`
- Extends TL;DW `source-item-row.tsx`'s active/paused `Switch` → a 3-way control.
- **Open Q (spec §7):** how "Only their big stuff" is determined — engagement threshold? duration? topic match? (Decide in Phase 5E; record here.)

### 2.3 Topic ribbon (spec §5.3)
A draggable **30-cell color-coded ribbon**; each top-level category (the pinned 8 — C1) is a color. Drag boundaries to rebalance topic attention.
- ⚠ The spec calls this "existing design" — **locate that artifact** before building (open Q #5 in the plan doc).
- The ribbon allocates only the slots **left over** after sources claim theirs (§2.4 below).

### 2.4 Presets (spec §5.4)
Three one-tap presets that set the master dial + sane defaults:
- **Power Feed** — mostly sources (dial left).
- **Balanced** — middle.
- **Wide Lens** — mostly discovery (dial right).
Manual controls remain available beneath.

---

## 3. Allocation algorithm (the `daily_feeds` input)

### 3.1 Pinned-first fill (30 slots)
```
N = 30
sourceBudget = round(N * (1 - masterDial/100))   // dial 0 → 30 source slots; dial 100 → 0
topicBudget  = N - sourceBudget

// 1) Sources fill first, by priority + freshness
pinned = []
for source in followedSources where priority != Off, ordered by (priority desc, freshness):
    take new content_items up to the source's allowance      // "big stuff" = filtered subset; "everything" = all fresh
    pinned += claimed slots, capped at sourceBudget
sourceSlots = len(pinned)

// 2) Topics fill the remainder (sourceBudget underflow rolls into topics)
topicSlots = N - sourceSlots
allocate topicSlots across categories by the ribbon's per-category weights
// (reuse ranking-spec.md per-(user,story) Score within each category)

// 3) No double-counting: a story already claimed by a source is not re-counted under its topic
```

### 3.2 Sparse-source handling (resolve the §7 open Q)
If followed sources produce fewer fresh items than `sourceBudget` on a given day:
- **Recommendation: soft bias.** Unfilled source slots roll into `topicBudget` (the feed stays full at 30) rather than leaving gaps. The dial expresses *intent/ceiling*, not a hard floor.
- Record the final decision here once locked in Phase 5E.

---

## 4. Live preview (spec §5.6 — the key UX detail)

The 30-cell ribbon is a **live preview**, not a static control. On every drag (master dial, a source priority, a topic boundary) it **re-renders instantly**:
- **Source-driven cells** show a tiny avatar (reuse `source-artwork.tsx`).
- **Topic cells** show solid category color.

Implementation: the allocation in §3 runs client-side on each change against a cached snapshot of available `content_items` per source + per category, so the preview is immediate (no round-trip). The committed allocation persists to the user's prefs and drives the next `daily_feeds` build.

---

## 5. Ordering & learning (spec §6 → M6 / Phase 6B)
- **v1:** order is manual (dial + ribbon + priority).
- **Then:** learn ordering from engagement — **watch-completion + questions-asked + follow/unfollow only**. **NOT gestures** (C2 — swipe is navigation, not preference; spec §6's "gesture usage" is dropped). Reuse the M1 bounded/decayed signal→weight loop (`reference/ranking-spec.md`).
- Never hard-code order (e.g. "geopolitics always first 4"); adapt to where engagement concentrates.

---

## 6. Reuse / build

| Piece | Source | Decision |
|---|---|---|
| Per-source priority row | TL;DW `source-item-row.tsx` (active/paused `Switch`) | **ADAPT** → 3-state |
| Avatar in preview cells | TL;DW `source-artwork.tsx` | **PORT** |
| Master dial, 30-cell ribbon, presets, allocation fn, live preview | — | **NEW** (no donor) |
| Allocation → feed | `reference/ranking-spec.md` (`daily_feeds`) | **ADAPT** (sources-first as input) |

## 7. Open questions (→ Phase 5E / `/cmo`)
1. Master dial: hard split or soft bias (§3.2) — recommend soft.
2. "Only their big stuff" threshold definition (§2.2).
3. Locate the "existing" 30-cell ribbon design artifact (§2.3).
4. Where the per-source-per-day available-content snapshot for the live preview comes from (cache shape).
