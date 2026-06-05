# Onboarding Interest Picker — Build Specification

**For:** Claude Code
**Feature:** First-run onboarding interest selection
**Status:** Ready to build. A working reference prototype exists (`interest_picker.html`) — treat it as the canonical source of truth for behavior and visual design. This document explains the *why* and the contracts so you can port it into the real app stack.

---

## 1. Context

We're building an AI-powered personalized news app: a swipeable, short-video news digest feed. The feed personalizes to each user's interests. This feature is the **onboarding interest picker** — the screen(s) where a new user tells us what they care about, which seeds their explicit personalization profile.

Two things matter about where this sits:

- The feed always opens with 3–4 **breaking/important stories regardless of interest** (an editorial override layer). This means a user who selects *nothing* still gets a usable feed. The picker is therefore high-value but not a hard gate.
- Onboarding produces the user's **explicit** follow signals. Later, the feed layer adds **implicit** signals (watch completion, questions asked, follow/unfollow over time). Do not conflate the two systems; this spec covers only the explicit onboarding capture.

> **Cross-cutting principle (do not violate):** swipe gestures in this app are *navigation*, not preference. Nothing in onboarding should train on or infer preference from gestures. Preference comes only from explicit follows captured here and explicit follow/unfollow later.

---

## 2. The one architectural principle that drives everything: Topics vs Entities

Every selectable thing is one of two types:

- **Topic** — stable, finite, hardcodable. Example: "Inflation", "Wildfires", "Computer vision". These can live in a static config and stay valid for years.
- **Entity** — dynamic, effectively unbounded, changes over time. Example: a company (Nvidia, NVDA), a sports team (Kansas City Chiefs), a person (Patrick Mahomes), an artist (Taylor Swift), a named conflict. These **must be backed by a live entity registry**, never frozen into the codebase.

This split decides where data comes from. Topic nodes ship in config. Entity nodes ship with a small *seed* list for first paint, but their full universe is served by the registry (see §6). Tag every node with its `type` and, for entities, a `kind` (`company | team | person | league | org | asset | event | brand | franchise | conflict | genre | product`). The UI uses `kind` for affordances (e.g., companies render a ticker).

---

## 3. Information architecture

Eight top-level categories (Health was intentionally removed):

`AI · Geopolitics · Business · Environment · Politics · Tech · Sport · Arts`

The tree is conceptually **Category → Subcategory → followable items**, but the engine must be **recursively nestable to arbitrary depth**, because some branches go deeper:

- Business → Corporate news → *Earnings* → **Companies (with tickers)**
- Business → Energy & commodities → *Oil & gas* → **Majors / Midstream & pipelines / Equipment & turbines**
- Sport → American football → *NFL* → **Teams** + **People**
- Arts → Music → *(genre)* → **Artists & bands**

There is no fixed maximum depth. League → team → individual player is just more data on the same engine, not new code.

---

## 4. Interaction model

The entire UI is built from one repeating unit: the **follow-set**.

A follow-set is a labeled group of **bubbles** (selectable pill chips). Every follow-set, without exception, provides:

1. **Tap-to-toggle bubbles** — tapping selects/deselects; selected state is visually distinct.
2. **Select all** — toggles every bubble in that set on (or off if all are already on).
3. **Show more** — reveals additional items. In the prototype these are static; in production this **paginates the registry** for that node.
4. **Add your own** — a free-text input that adds a custom bubble (auto-selected). In production this **searches the registry**; the typed value resolves to a real entity where possible, otherwise is stored as a free-text follow.

**Nested reveal:** selecting certain bubbles unfolds one or more child follow-sets directly beneath the bubble. Example: selecting `NFL` reveals a *Teams you follow* set and a *People to follow* set. Deselecting collapses them (but should preserve any selections made inside, in case the user re-selects). Nesting is recursive.

**Cross-follow falls out for free:** because each league exposes its own People set, a college-football fan can also open NFL and follow an NFL person. No special-casing needed.

### Required behaviors to verify (these are the cases the product owner cares about)

- Corporate news → tap **Earnings** → nested *Companies to track* appears with ticker symbols, Select all, and Show more.
- Energy & commodities → tap **Oil & gas** → three nested sets appear (Majors, Midstream & pipelines, Equipment/turbines/services).
- American football → tap **NFL** → *Teams* (with Show more) **and** *People* appear; College football behaves the same, independently.
- Music → tap a **genre** → that genre's artists unfold; user can multi-select across multiple genres and add their own.

---

## 5. Data model

Recursive. Topics ship inline; entity sets ship with seeds + a registry pointer.

```jsonc
// A node (a selectable bubble OR a navigational container)
{
  "id": "string",                 // stable, path-derived: e.g. "business/corporate-news/earnings"
  "label": "string",
  "type": "topic" | "entity",
  "kind": "company|team|person|league|org|asset|event|brand|franchise|conflict|genre|product", // entities only
  "ticker": "AAPL",               // companies only
  "sets": [ FollowSet ]           // optional: child sets revealed when this node is selected
}

// A FollowSet
{
  "id": "string",
  "label": "Companies to track",  // shown as the set's eyebrow label
  "items": [ Node ],              // seed bubbles, shown immediately
  "registry": {                   // present on entity sets; absent on pure-topic sets
    "parent": "business/corporate-news/earnings",  // scope passed to registry calls
    "kind": "company"
  },
  "allowCustom": true             // always true per product requirement
}
```

Categories and subcategories are just nodes whose `sets` are shown when expanded. IDs are derived from the label path (slugified) so they're stable and human-readable in analytics.

The full seed dataset used by the prototype is embedded in `interest_picker.html` — lift it from there rather than re-authoring.

---

## 6. Data layer / registry contract

Topics come from static config. Entities use seeds for first paint and the registry for everything else. Two endpoints power **Show more** and **Add your own**:

```
GET /api/entities/list?parent={nodeId}&kind={kind}&cursor={cursor?}&limit=20
→ { "results": [ { "id", "label", "ticker?", "kind" } ], "nextCursor": "string|null" }

GET /api/entities/search?q={query}&kind={kind?}&parent={nodeId?}&limit=20
→ { "results": [ { "id", "label", "ticker?", "kind" } ] }
```

- **Show more** calls `list` with the set's `registry.parent`, appending `nextCursor` pages until exhausted.
- **Add your own** debounces the input and calls `search`; show matches as suggestions. If the user picks a match, store the resolved entity `id`. If they submit free text with no match, store it as `{ kind: "freetext" }` — still a valid follow.
- Seeds should be served from CMS/config so the team can curate "top N" defaults per node without a deploy. Do not over-build the registry: it can start as a curated table and grow; avoid standing up heavyweight new pipelines for v1 (keep cost and surface area small).

---

## 7. Output: the personalization payload

On completion, persist the user's follows. This is the contract the personalization/feed system consumes.

```
POST /api/users/{userId}/follows
[
  {
    "followId": "business/corporate-news/earnings/companies-to-track/nvidia",
    "label": "Nvidia",
    "path": ["Business", "Corporate news", "Earnings", "Nvidia"],
    "type": "entity",
    "kind": "company",
    "ticker": "NVDA",
    "source": "seed" | "more" | "custom"
  }
]
```

`source` is a signal worth keeping: a `custom` follow (the user typed it) is higher-intent than a seed tap and should weight more heavily in ranking. `path` gives the feed both the specific follow and its ancestry for fallback/related content.

---

## 8. Visual design system (must follow)

The aesthetic is **refined editorial**, not generic AI/SaaS. Do not use Inter/Roboto/Arial, purple-on-white gradients, or default component-library looks. Match the approved prototype exactly. Use CSS variables (or your platform's token equivalent).

**Design tokens (from the approved prototype):**

```css
--bg:   #f4f1ea;  /* warm cream background */
--ink:  #1b1a17;  /* near-black text & borders */
--muted:#6f6a5e;  /* secondary text, eyebrow labels */
--line: #dcd6c8;  /* hairlines, unselected chip borders */
--card: #fffdf7;  /* surfaces */
--sel:  #3a5a40;  /* selected chip fill (deep green) */
--sel-ink:#fffdf7;/* text on selected chip */
--rust: #9a4a1f;  /* accent: tickers, entity emphasis, counts */
```

**Typography:**
- Display / headings: **Fraunces** (weights 600/900).
- Body / chips: **Spline Sans**.
- Eyebrow labels, tickers, counts, buttons: **Spline Sans Mono** (uppercase, letter-spaced).
- Provide system-font fallbacks for offline/load failure.

**Chips:** pill-shaped, 1.5px border. Unselected = card fill + `--line` border. Selected = `--sel` fill, `--sel-ink` text. Custom-added chips = dashed border. Tickers render in `--rust` mono, inline after the label. Hover/press states required. **Mobile-first: minimum 44px touch target height; chips wrap fluidly.**

**Set chrome:** eyebrow label in muted mono caps; "Select all" / "Show more" as small outlined pills; nested sets indented with a 2px left rule in `--line`.

**Persistent tray (bottom):** dark (`--ink`) bar showing total follow count (Fraunces), a live preview of recent picks, per-category counts in each category header, a "Review" panel (grouped by category), and "Copy/Export" of the payload. Keep it fixed and unobtrusive.

**Motion:** restrained. Chevron rotate on expand, a quick fill transition on chip select. No gratuitous animation.

---

## 9. Suggested component breakdown

- `OnboardingPicker` — page container, owns the selection store and the tray.
- `Category` — collapsible section; shows per-category follow count.
- `Subcategory` — collapsible; renders its follow-sets.
- `FollowSet` — eyebrow + Select all + Show more + chip grid + Add-your-own; owns its registry pagination/search.
- `Chip` — toggle button; renders ticker; on select, mounts its nested `FollowSet`s.
- `SelectionTray` — count, preview, review panel, export.

Selection state: a single store keyed by `followId` (the prototype uses a `Map`). Nested sets should mount lazily on first reveal and preserve internal selections across collapse/expand.

---

## 10. Definition of done

- [ ] All 8 categories render; Health is absent.
- [ ] Bubbles toggle; selected state matches tokens; state survives collapse/expand of parents.
- [ ] Every follow-set has working Select all, Show more, and Add your own.
- [ ] Nested reveal works for the four marquee cases in §4.
- [ ] Companies display ticker symbols.
- [ ] Show more paginates the registry; Add your own searches it and resolves matches; free text is accepted.
- [ ] Tray shows total + per-category counts + review panel; export emits the §7 payload.
- [ ] On completion the payload POSTs to the follows endpoint; user proceeds to feed.
- [ ] Skippable: skipping yields an empty follow set and a breaking-news-only feed (no error state).
- [ ] Mobile-first, ≥44px touch targets, fully keyboard/screen-reader accessible (chips are real buttons with `aria-pressed`).
- [ ] Typography and palette match the prototype; no generic AI styling.

---

## 11. Non-goals & edge cases

**Non-goals (v1):**
- No swipe-based preference inference (swipe is navigation only).
- No implicit-signal capture here (that's the feed layer).
- No heavyweight new data pipelines for the registry; a curated, paginable table is fine to start.
- Health category is out of scope.

**Edge cases:**
- Zero selections → valid; proceed to breaking-news-only feed.
- Selecting a parent then deselecting → collapse nested sets but retain their internal selections in the store until the parent is fully removed (decide and document one behavior; prototype keeps child selections).
- Duplicate entity reachable via two paths (e.g., Nvidia under AI hardware *and* Tech semiconductors) → store as the same underlying entity id with multiple `path`s; dedupe in the payload.
- Registry/search offline → fall back to seeds; Add-your-own still accepts free text.
- Very long custom input → trim/validate sensibly.

---

## 12. Reference

`interest_picker.html` is the working, approved prototype. It is the authoritative reference for: the recursive engine, all interaction behavior, the full seed dataset, and the exact visual design. When this document and the prototype agree, follow them. When porting to the production stack, preserve behavior and look; swap the static seeds/Show-more for the registry endpoints in §6 and wire the §7 payload on completion.
