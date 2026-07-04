# Chat Onboarding Spec — One-Conversation Interview + Source Selection

**Why this doc exists:** single source of truth for the chat-onboarding contract between the SPA onboarding stage and the worker's interview engine — phases, turn protocol, guardrails, skip semantics, terminal schema, persistence mapping. `/to-issues` slices against it; builders test against it.
**When to update:** any change to the phase order, turn protocol, bubble/TUNE guardrails, skip rules, terminal schema, or persistence mapping. Update alongside `supabase-schema.md` and `api-contracts.md` when migrations land.

> **Supersedes** the 2026-07-03 single-select tap-through spec (rejected by the founder; recoverable at git `7ff7c68`). The engine's stateless turn protocol, guardrails, terminal validation, and persistence mapping from that spec **carry forward**; the interaction layer and phase structure are new.
> **Interaction reference (canonical):** `reference/design-handoff/onboarding-chat.{html,js}` — keep its interaction contract; replace its canned copy/branching with the engine. Where the handoff conflicts with `blip-design-guide.md`, the guide wins.

## 1. Flow position

The chat absorbs the `interview`, `sources`, and `build` stages of `OnboardingFlow.tsx` into ONE stage:
`splash → email → wait_session → **chat** → loading → done`.
`users.user_onboarded_at` stamps only at true flow end (`markOnboardingComplete()`) — never mid-chat (onboarding-gate rule, 2026-06-30). The M6a sources screen and Build-your-30 remain as later in-app editing surfaces, no longer onboarding steps.

## 2. Phases (one scrollback, five phases)

| Phase | What happens | Rendered as |
|---|---|---|
| **INTERESTS** | Multi-select category chips (the 8 backend roots — see §3) → per selected category: multi-select sub-niche chips + **live composer** for typed custom interests → **one open-ended WHO drill per selected sub-niche** ("Cricket — a series, a player? Name it"), skippable via "Nothing specific →" | chips + composer + open drills |
| **TUNE** | 3–4 quick single-tap follow-ons per category, dynamically worded, covering two **code-enforced fixed jobs**: **ANGLE** (which lens grabs you) and **SKIP** (mute list — absent from the prototype, mandatory here). Then the **story budget card**: ± steppers across selected categories, news total pinned (default 20), "Lock →" | quick replies + in-chat card |
| **YOUTUBE** | ~30 channel tiles sorted by profile relevance, multi-select, with **supply expectation** line ("these 12 channels ≈ ~5 long-form videos/day", from catalog cadence estimates — no live calls) | in-chat tile grid |
| **X CLUSTERS** | Curated handle clusters grouped by the user's categories + a "beyond your picks" group; samples + expandable handle list; multi-select | in-chat checklist |
| **YOUR 30** | Summary card: news / YouTube / X split (default **20/7/3**), re-mixable 0–30 per axis, total pinned at 30 → "Build my 30 →" | in-chat card |

Answered steps collapse into user bubbles; history stays visible; no screen swaps.

## 3. Category chips = the 8 backend roots

`ai, geopolitics, business, environment, politics, tech, sport, arts` (labels from `agents/interview/constants.py::ROOT_LABEL_BY_SLUG`). The prototype's 9-category list is illustrative only. Health has no root in v1 — health-type typed input parks under the nearest root or root-level.

## 4. Turn protocol (worker endpoint — carried forward, extended)

One turn per request; server-driven; client is a dumb renderer. `POST /api/interview/turn`.

- **Request:** conversation state so far — ordered exchanges (question, bubbles offered, bubbles tapped **[multi]**, free text). No server session; worker stateless.
- **Response, non-terminal:** next turn, typed by kind: `category_chips` | `subniche_chips` (composer live) | `who_drill` (open) | `tune_quick` (single-tap) | `budget_card` | `youtube_grid` | `cluster_list` | `summary_card`.
- **Response, terminal:** the extracted profile (schema §6) for confirmation.
- **Failure contract:** HTTP 200 always, typed retry/error body; TUNE turns have **deterministic fallback wording** if the LLM fails; a failed turn is never a dead-end screen.
- **Auth:** user JWT (same seam as `/feed/assemble-mine`).

## 5. Guardrails + skip semantics (code-enforced, never model-enforced)

| Rule | Value |
|---|---|
| Turn-1 bubbles | the 8 fixed roots (no LLM call) |
| Generated bubbles per turn | ≤ 6, + always skip + always type-your-own |
| WHO drills | exactly one per selected sub-niche; if total drills would exceed ~8, compress remaining sub-niches into one combined drill |
| TUNE per category | 3–4 questions; ANGLE + SKIP jobs always covered |
| Free-text gibberish | one clarifying turn, then park as root-level |
| Model | Gemini Flash-class, structured JSON, low temperature |

**Skip fast-forward (engine state machine, deterministic):** every question skippable → skip the first follow-up of a category ⇒ offer to skip the category → two consecutive categories skipped ⇒ offer "just build my feed". Skipped questions are recorded as **deferred** at terminal persist (in-app resurfacing UI is a fast-follow; the record ships now). Skip-everything ⇒ roots-only profile (feed works as today).

**Re-run:** rebuild-my-feed entry runs the chat with **clean-replace** semantics — no orphaned mutes, follows, or allocation rows.

## 6. Terminal output schema

Per micro-interest (unchanged from the shipped engine): `display_label` (user's vocabulary), `canonical_slug` (root-anchored dotted), `ladder` (tap-path parent chain = fallback ladder), `search_anchor_terms` (≥ 2 concrete terms), `strict`.

New alongside the interest list:
- `mute_terms` — per category, from SKIP answers (hard filters at assembly, never at ingestion).
- `angle_preferences` — per category, from ANGLE answers.
- `deferred_questions` — skipped questions for later resurfacing.
- `news_budget` — per-category counts from the budget card (sums to the news total).
- `source_follows` — selected channel ids + cluster ids.
- `top_split` — news/youtube/x counts summing to 30 (default 20/7/3).

Validation before persistence (carried forward): root-anchored slug parses; ≥ 2 anchor terms; every item traceable to a tap or typed text — nothing invented; invalid items re-asked or dropped loudly. Raw chat transcript is NOT persisted.

## 7. Persistence mapping

| Output | Lands in |
|---|---|
| minted niche nodes | `interests` (global, upsert by slug) |
| picks + weight + strictness | `user_interest_profile` |
| mute terms | `user_mute_terms` (new: user, category, term) |
| news budget + top split | `user_feed_allocation` (news category rows + `youtube` + `x` rows, sum = 30) |
| channel follows | `user_content_sources` |
| cluster follows | `user_content_sources` rows for members **+** cluster ref (new `user` link to `source_clusters`) for sweep scheduling + theme attribution |
| deferred questions | new deferred-skips store (shape decided at slice time) |
| angle preferences | with the profile (shape decided at slice time) |

## 8. Assembly consumption (summary)

Niche sections fill per the shipped niche-first + honest-ladder assembly (slice #7). Mutes filter hard at assembly. Source slots fill from YouTube/X reels per `reference/source-reels-spec.md`; any unfillable source slot rolls to news (existing soft-roll). All-news and all-X allocations are valid.
