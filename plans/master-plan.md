# Master Plan

**Date:** 2026-05-28
**Source brief:** documents/product-brief.md
**Status:** Active

## Design alignment (2026-05-29)

A design prototype handed off from Claude Design (vendored at `prototype/`) re-directed four things this plan must reflect:

1. **Brand:** the product is now **blip** (an audio/radar "signal" — fits an audio-first app); the codename/repo stays **News20**.
2. **Audio-first reel:** the audio digest is the hero. **Karaoke Playfair captions** (sentence dim → current word white → exactly one `#FACC15` yellow keyword/sentence) sync to the TTS audio; the poster image recedes to a blurred, drifting, segment-accent ambient wash.
3. **Onboarding:** a **chip-based interest picker** (hierarchical tappable **category → subcategory → sub-subcategory** chips with niche-down drill-down) replaces the checkbox grid. *(Updated 2026-05-30: the voice-agent interview was dropped — onboarding is chip-only. In-news Voice mode (swipe-left Q&A) is unaffected.)*
4. **Auth:** **email-only passwordless magic-link** (Supabase email OTP); Sign-in-with-Apple removed (avoids the review/entitlement overhead; re-add only if App Store review demands it).

Source of truth: `prototype/` (runnable prototype + `ui-design-decisions.md` + `chats/`), plus `reference/supabase-schema.md` and `reference/prototype-port-map.md` (both being written now). This pivot revises Key design decisions #3 and #7 below — see those entries.

## Vision (one paragraph)

**blip** (codename/repo News20) is a swipeable, auto-playing iPhone news app for the 25–34 commuter who doom-scrolls but feels guilty they're not keeping up. It turns each day's stories into ~55-second AI "anchor-duo" digests (two consistent Gemini TTS voices over still images with motion, sound-off captions) that auto-advance hands-free — ~30 stories in ~30 minutes and you're caught up. The defensible moat is the **interrogation layer**: swipe right into a Story Detail View with a readable chunked text version, supporting visuals, a trust layer (coverage breakdown / blindspot / opposing view), and a search box to *ask the story questions* grounded in the source; swipe left for the same conversation hands-free by voice. It wins against the "do-nothing scroll reflex" by being a finite, completable loop, and against the TikTok FYP by letting you interrogate a story instead of hitting a dead-end clip.

## Tech stack

- **Frontend:** Next.js 15 (React 19) + Tailwind 4 + radix-ui + framer-motion, built as a static SPA and wrapped in **Capacitor** for a native iPhone App Store binary. Rationale: keeps near-total reuse of TLDW's web UI scaffolding while delivering an App-Store-distributable native shell with mic + native video access. *(Flagged for revisit per Rule 7: Expo/React Native would feel more native but forfeits TLDW frontend reuse — chosen against for v1.)*
- **Backend:** Supabase (Postgres + auth + storage/CDN). Rationale: relational story/source/signal modeling + file storage for rendered MP4s; already the TLDW backend, so schema patterns and client setup port directly.
- **Agents:** Python agents (Pydantic-typed), reused wholesale from TLDW. Rationale: the TTS, grounded Q&A (chat + verification stage), scripting, and ingestion logic already exist and are the expensive parts — see `reference/reuse-map.md`. *(The TLDW RAG/vector layer is **not** reused for M2 — the per-story corpus is loaded into context instead; see Decision #5.)*
- **Jobs:** Trigger.dev v4 (`@trigger.dev/sdk` v4 `task`/`schedules.task`). Rationale: the once-per-story generation pipeline is a scheduled fan-out job; TLDW already wires Trigger.dev v4.
- **Hosting:** Supabase (data/storage) + a Python worker service (FastAPI, Docker → Railway/Fly, as in TLDW) for the agent pipeline + Remotion render; Next.js API surface on Vercel for the SPA's data calls. iOS binary distributed via App Store.
- **Languages:** TypeScript (frontend + Remotion + Trigger tasks) and Python 3.12 (agents/pipeline).

## Architecture

```
                       ┌──────────────────────────────────────────┐
 iPhone (App Store)    │  Capacitor iOS shell                      │
                       │   └─ Next.js SPA (React 19 + Tailwind)    │
                       │       • Swipe reel — auto-play <video>     │
                       │       • Story Detail — read + visuals      │
                       │       • Search box — typed grounded Q&A     │
                       │       • Voice mode — mic → Gemini Live (WSS)│
                       └─────────────────┬────────────────────────┘
                                         │ HTTPS / WSS
                       ┌─────────────────▼────────────────────────┐
                       │  Supabase (auth · Postgres · storage/CDN) │
                       │   users·interests·follows·signals          │
                       │   stories·digests(mp4_url)·sources·bias    │
                       └─────────────────┬────────────────────────┘
                                         │
      ┌──────────────────────────────────┼──────────────────────────────────┐
      │ Trigger.dev v4 (once-per-story)   │  Python worker (FastAPI, reused)   │
      │  ingest→rank→script→tts→align→     │   • voice/gemini_tts (anchor duo)  │
      │  image-source→Remotion render→     │   • pipeline/stages: script,align, │
      │  store MP4 → bias→coverage/blindspot│     verify, rank                   │
      │                                    │   • ingestion/adapters (news)      │
      │                                    │   • qa: load per-story corpus      │
      │                                    │     into context + verify (no DB)  │
      └───────────────┬────────────────────┴───────────────┬───────────────────┘
                      │                                     │
            ┌─────────▼──────────┐               ┌──────────▼─────────┐
            │ External APIs       │               │ Grounded Q&A/voice │
            │ NewsAPI·MediaStack· │               │ per-story corpus   │
            │ AlphaVantage·HN·PH  │               │ in-context+verify  │
            │ Pexels·Unsplash·    │               │ (NO vector store)  │
            │ Pixabay (images)    │               └────────────────────┘
            │ Gemini TTS + Live   │
            │ AllSides/AdFontes   │
            │  (static bias map)  │
            └─────────────────────┘
```

**M5/M6 additions (two-axis personalization — see `plans/m5-m6-personalization-sources-control-surface.md`):** the architecture above gains (1) **source ingestion adapters** — YouTube (upload-detect + caption transcript), podcast (RSS→Whisper), and a build-fresh **X/Twitter** monitor — feeding the same deduped story pool; (2) a **recommendation engine** (interest-vector → nearest **archetype** → Jaccard-ranked curated source catalog, refreshed by a build-fresh community-signal **research agent**); and (3) an **allocation layer** that fills the 30 slots **pinned-sources-first, topics-fill-the-rest** into `daily_feeds`, driven by the user's **control surface** (master dial + per-source priority + topic ribbon).

## Key design decisions

1. **Capacitor wrapping a Next.js static SPA** for the iPhone app. Why: App-Store-native distribution + native mic/video without rewriting the React UI in RN. Rules out: a separate Expo/React Native frontend (and its loss of TLDW reuse).
2. **Inherit the TLDW stack wholesale** (Supabase + Trigger.dev v4 + Python agents). Why: the costly pieces (multi-speaker TTS, grounded Q&A, ingestion) already exist and are battle-tested. Rules out: Convex, a fresh backend, LangChain. *(Revised 2026-05-31: **Pinecone dropped.** TLDW's RAG/vector layer was for a large corpus; News20's Q&A grounding is per-story + single-source + "<100s read" — a few hundred words — so the story's corpus loads directly into the LLM context (verification-gated), no vector store. See Decision #5 + `plans/phase-2b-m2-grounded-interrogation.md` re-scope. Reintroduce retrieval only if a story's corpus outgrows the context window.)*
3. **One canonical audio digest + word-timed caption JSON + poster image per story, served to many users by interest match** — never per-user generation. Why: unit economics; the brief's core viability constraint. *(Revised 2026-05-29 per the audio-first pivot: the shipping reel is no longer a pre-rendered 9:16 MP4 — it composites **live in the client** from the audio + word-timestamped caption JSON + an ambient poster wash. Same one-canonical-asset-per-story unit economics; the asset is now a small audio+JSON+image bundle, not a video file.)* *(Clarified 2026-05-30 re-scope: "served to many users by interest match" is implemented via a **deduped story pool + `story_interests` fan-out** — a story is produced **once** and shared with every user whose interest it serves; a user with a genuinely unique interest may be the only consumer of a given story, but it is still one canonical asset per story, never the same story re-generated per user.)* Rules out: per-user re-generation of the same story.
4. **Digest scripts are constrained to a single source article.** Why: hallucination control in the generated narration. Rules out: multi-source synthesis in the script stage.
5. **Interrogation (typed + voice) is source-grounded + passes a verification stage.** Why: news has zero tolerance for invented facts (brief Open Q5); answers must cite the source, and the model must refuse when the source can't support an answer. *(Revised 2026-05-31: grounding is done by **loading the story's whole (small) corpus into the LLM context** and constraining the model to answer only from it — not vector retrieval. The corpus is tiny (per-story, single-source), so this is simpler and makes the zero-hallucination guarantee stronger — the model sees the whole source, no retrieval-miss failure mode. Verification still gates every claim.)* Rules out: free-form ungrounded LLM answers in Q&A/voice; a vector DB for M2-scale corpora.
6. **The trust layer derives from a static outlet→bias lookup table** (AllSides / Ad Fontes) joined to a **GDELT coverage census**. Why: the static table maps outlet→lean at near-zero per-story cost; GDELT (free DOC 2.0 API) supplies the *breadth* — who actually covered a story — far beyond the few articles ingestion clustered. The two together give coverage breakdown, blindspot flag, and "opposing view". *(Revised 2026-05-31, `plans/phase-2c-m2-detail-analytics-pipeline.md`: Coverage is now **adaptive** — `partisan` mode (L·C·R + blindspot) for contested/geopolitics stories, `reach` mode (covered-by-N + momentum + who-broke-it) elsewhere, since L/R lean is meaningless on a sports score or a science result. GDELT is the census source; the static bias table is still the only lean source.)* Rules out: licensed per-outlet factuality / ownership data; a per-story bias API call.
7. **Remotion DEMOTED to OPTIONAL share-export / social clips — NOT the in-app reel.** *(Revised 2026-05-29 per the audio-first pivot.)* The in-app reel composites **live in the client** (TTS audio + word-timed caption karaoke + an ambient drifting poster wash); it is **not** a Remotion-rendered MP4. Remotion (still images + Ken Burns, no AI video, no avatars) is retained only for an optional out-of-app 9:16 MP4 used for sharing / social clips. Why client-live for the reel: the audio-first format wants per-word caption timing and a calm ambient wash that the client can drive directly, and it removes per-story video render cost/latency from the critical path. **⚠️ CONFLICT FLAGGED (Rule 7/12):** this contradicts the prior framing where Remotion rendered the canonical in-app reel and the M0/Phase-0 work is described as "render 5 MP4s." Resolution: client-live wins for the *shipping* reel (it's the more recent, owner-directed, audio-first decision); Remotion is kept but demoted to share-export. **M0's MP4 render still has value as a *content-quality* test** (does the script/voice/pacing read well?) — so **do NOT delete Phase 0 or its progress file**; treat M0 as a format/quality gate, not the production renderer. **`/plan-phases` should re-plan M1 and M3 against the new reference docs** (`reference/prototype-port-map.md`, `reference/supabase-schema.md`) before either is built. Rules out: Sora/Veo/Runway video, HeyGen/Synthesia avatars, and a pre-rendered MP4 as the in-app reel.
8. **Personalization = category prioritization from engagement signals** (reuse TLDW `memory/player_signals`), not an ML model. Why: simplicity for v1; surface the categories the user engages with first. Rules out: the 6-signal behavioral ML described in the report. *(Re-scoped 2026-05-30: personalization is now **M1, not M3**, and is interest-keyed per `reference/ranking-spec.md` — a heuristic per-(user,story) Score + a ~30-slot per-user allocation precomputed into `daily_feeds`, with a bounded/decayed signal→weight loop. Still no ML.)* *(Extended 2026-06-04 for M5/M6: personalization gains a **second axis — sources** (YouTube/podcast/X/personalities) alongside topics, an **archetype-profile** mapping for instant source recs, and a **control surface** (master dial + per-source priority + 30-cell allocation ribbon) governed by the **pinned-sources-fill-first** rule. Still heuristic, no ML. See Decisions #11/#12 + `plans/m5-m6-personalization-sources-control-surface.md`.)*
9. **A quality spike (M0) gates the full build.** Why: the brief's riskiest assumption is that static-images + two-narrator digests feel too cheap to retain. Produce ~5 real digests, test on strangers, before committing to the full pipeline. Rules out: building all of M1–M4 on an unvalidated format.
10. **Story Detail is a 3-slot tabbed analytic: `[ Story Timeline | «second analytic» | Coverage ]`.** *(Added 2026-05-31, `plans/phase-2c-m2-detail-analytics-pipeline.md`.)* Story Timeline is universal; the **second analytic** is skinned by `story_segment_slug` — `market_impact` (geopolitics), `ripple` (markets), `impact` (tech), `stakes` (sport), `why_it_matters` (wildcard) — chosen **deterministically in code, not by the LLM** (Rule 5); Coverage is the adaptive partisan/reach tab (Decision #6). Above the tabs sits a hero **key figure**; below them, **5 at-a-glance bullet points** then "Read the full article" (the long-form `detail_chunks`). The timeline, key figure, second analytic, and bullets are **LLM-generated and grounded against the single source** (Decision #4/#5); **market numbers are grounded-or-omitted** — a fabricated figure is worse than none. Why one generic `story_analytics` shape (not bespoke per-category tables): one renderer + one pipeline stage serve all five segments. Rules out: bespoke per-category tab *sets*; LLM-fabricated market numbers; a "Coverage" tab forced onto stories with no partisan axis.
11. **Sources are a first-class second personalization axis (topics ⨉ sources).** *(Added 2026-06-04, M5.)* A story can be selected by **what** it's about (topics/entities from the recursive interest picker) or **who** it comes from (a followed YouTube channel, podcast, X handle, or named personality whose fresh content is ingested into the digest pool). The two axes are resolved by one rule — **pinned sources fill slots first; topics fill the remainder** of the fixed 30-story window (no double-counting). Crucially, **follow-as-filter (a topic/entity follow → ranking signal) and follow-as-source (an ingestable feed → `user_sources`/`user_personalities`) are separate concerns in the schema** — the same person can exist on both axes without collision. Rules out: one slider that makes topics and sources fight; conflating a topic-entity follow with a content-source follow.
12. **Inherit TLDW's source stack wholesale** (mirrors Decision #2 for the news stack). *(Added 2026-06-04, M5.)* The source model (`sources`/`user_sources`/`personalities`/`content_items`), the archetype-keyed curated catalog + Jaccard recommendation matcher, YouTube upload-detect + caption transcription, podcast RSS→Whisper ingestion, the ingestion/refresh crons, and the source UI components all port from TLDW (file:line map in `reference/sources-reuse-map.md`). **Build-fresh (no donor):** X/Twitter monitoring (only the `TwitterContentMetadata` shape survives), the community-signal research agent, and the entire allocation control surface (master dial / 30-cell ribbon / pinned-first). Re-skin all lifted UI to News20's design language — do **not** carry TLDW's editorial-dark amber palette. Rules out: green-fielding the source pipeline; carrying TLDW's palette.

## Milestones (not phases — phases come from /plan-phases)

- **M0 — Quality spike (de-risk gate):** ~5 real end-to-end digests produced (news article → script → anchor-duo TTS → images + Ken Burns + captions → MP4) and shown to strangers. *True when:* we have evidence people would watch 10 in a row. This validates the riskiest assumption before scaling.
- **M1 — Personalized audio-first karaoke reel MVP:** Email magic-link auth + chip-based 3-level interest onboarding → an **interest-keyed** once-per-story pipeline that ingests news per the union of users' interests into a **deduped story pool** and produces the canonical **audio digest + word-timed caption JSON + ambient poster image** in Supabase → a **per-user feed** (`daily_feeds`, scored + allocated per `reference/ranking-spec.md`) → Next.js swipe-up reel that **composites karaoke captions live in the client** (sentence dim → current word white → one `#FACC15` keyword/sentence) synced to the TTS audio over a drifting poster wash, running inside a Capacitor iOS build. *True when:* a new user can sign in, pick interests, and passively listen-and-read ~20–30 fresh daily stories **chosen for them** back-to-back, captions tracking the audio word-by-word. *(Re-scoped 2026-05-30: personalization + email auth + chip onboarding pulled forward from M3 — the app is never shipped anonymous. Voice-agent onboarding was dropped 2026-05-30 — onboarding is chip-only.)*
- **M2 — Detail View + trust + interrogation:** Swipe-right Story Detail (chunked readable text <100s, supporting visuals), the bias/coverage/blindspot/opposing-view trust layer, and the typed search-box Q&A grounded by loading the story's source corpus into the LLM context (verification-gated, no vector store). *True when:* a user can read, see who's covering a story, and ask it a question that's answered from the source.
- **M3 — Voice mode + follow:** Swipe-left Gemini Live voice mode (hands-free, RAG-grounded interrogation of a story) and follow-a-story + "what's new since you last watched". *True when:* a user can interrogate a story hands-free and track followed stories. *(Re-scoped 2026-05-30: email auth, chip onboarding, the interest profile, engagement-signal instrumentation, and interest-weighted ranking all moved to M1. **Voice-agent onboarding was dropped 2026-05-30** — onboarding is chip-only in M1; M3 now adds only the in-news voice layer + follow.)*
- **M4 — App Store ship:** TestFlight build, polish, accuracy/guardrail review, App Store submission. *True when:* the app is live and the 3x/week metric is instrumented.
- **M5 — Two-axis personalization (sources + control surface):** the recursive interest picker (topics/entities, arbitrary depth) replaces the M1 chip onboarding; a **sources axis** (YouTube/podcast/X/personalities) with archetype-mapped recommendations + search-add; ingestion of followed sources into the 30-story pool; and the **control surface** (master dial, per-source priority, 30-cell allocation ribbon with live preview, presets, pinned-sources-fill-first). *True when:* a user can follow topics **and** sources, see followed-source content in the feed, and rebalance the 30 slots between "my sources" and discovery. *(Added 2026-06-04. Moves YouTube/podcast ingestion in from Out-of-scope. ⚠ Picker placement undecided — the recursive picker may be pulled into M1 as an onboarding upgrade; pending owner call.)*
- **M6 — Discovery agent & learned ordering:** a community-signal **research agent** keeps per-archetype source lists fresh (crawl Reddit/X/podcast directories), and feed **ordering** moves from manual to engagement-learned (watch-completion + questions + follow/unfollow — **not** gestures). *True when:* newly-rising voices surface into recommendations without manual curation, and per-user feed order adapts to engagement. *(Added 2026-06-04.)*

## Phases

### M0 — Quality spike (de-risk gate)
- [Phase 0](phase-0-m0-quality-spike.md) — Render 5 real anchor-duo digests (TTS → forced-alignment captions → Remotion 9:16 compositor → 5 MP4s) for the "watch 10 in a row?" go/no-go gate.

### M1 — Audio-first karaoke reel MVP
- [Phase 1](phase-1-audio-first-reel.md) — Next.js static-SPA reel playing the 5 real M0 digests as fixtures: audio-driven karaoke captions, finite swipe loop, all-caught-up. *(front-loads the M1 experience risk; no backend)*
- [Phase 1b](phase-1b-supabase-backend-seed.md) — Supabase content schema + storage + public-read RLS + seed the 5 M0 digests + typed `getFeed()` data layer.
- [Phase 1e](phase-1e-auth-onboarding-interest-profile.md) — Email magic-link auth + 3-level chip onboarding (no voice) + persisted `user_interest_profile` + **migration 0003** (user/personalization schema + `story_interests`/`daily_feeds`).
- [Phase 1d](phase-1d-daily-content-pipeline.md) — Trigger.dev v4 **interest-keyed** daily pipeline: per-interest ingest → deduped pool + ancestor tagging → produce-once → score + allocate per user → `daily_feeds` (implements `reference/ranking-spec.md`).
- [Phase 1c](phase-1c-capacitor-ios-live-data.md) — Capacitor iOS build, auth/onboarding routing gate + **per-user `daily_feeds` read**, playing the signed-in user's feed back-to-back in the Simulator.
- _Suggested order: **Phase 1 ∥ Phase 1b** (parallel; share `src/types/feed.ts`), then **Phase 1e** (needs 1b; adds auth + profiles + migration 0003), then **Phase 1d** (needs 1e) ∥ **Phase 1c SP1–3** (needs 1e), then **Phase 1c SP4** (needs 1d's `daily_feeds`)._

### M2 — Detail View + trust + interrogation
- [Phase 2](phase-2-m2-detail-and-trust.md) — Swipe-right Story Detail: chunked Playfair body + key-figure card + bias/coverage/blindspot/opposing-view trust strip + expandable timeline (Supabase-direct reads).
- [Phase 2b](phase-2b-m2-grounded-interrogation.md) — Grounded typed Q&A: per-story corpus loaded into the LLM context (no vector store) + ported TLDW verification stage → citation-chip answers / `⌀ CAN'T ANSWER FROM SOURCE` refusal, persisted/cached to `story_qa` (the shared brain M3's voice reuses). *(Re-scoped 2026-05-31: dropped Pinecone/RAG — corpus small enough for in-context grounding.)*
- [Phase 2c](phase-2c-m2-detail-analytics-pipeline.md) — **Detail analytics data pipeline** (Decision #10): migration `0004` (`story_analytics` + `detail_key_points` + adaptive-coverage columns) + a pipeline enrichment stage (GDELT coverage census + grounded LLM key-figure/timeline/second-analytic/5-bullets) + the extended `StoryDetail` contract + seed. **Data path only — UI is owner-supplied.** *(Added 2026-05-31.)*
- _⚠ Phase 2 + 2b + 2c assume M1 foundations (Next scaffold, Supabase client + migrated/seeded content tables, LayerStack/reel, tokens) — **M1 is now planned** (`phase-1`/`1b`/`1c`/`1d`). Build M1 first._ Numbered `phase-2`/`phase-2b` (one integer per milestone, mirroring M0's `phase-0`/`phase-0b`) to avoid colliding with M1's `phase-1` group.

### M3 — Voice mode + follow (auth · chip onboarding · ranking now in M1)
> ⚠ **Depends on M1 (`phase-1`/`1b`/`1e`/`1d`/`1c`) + M2 (planned, not built).** M3's voice mode reuses M2's grounded answer endpoint (`phase-2b`, in-context grounding — not a vector DB); **auth, the user-side schema, chip onboarding, the interest profile, engagement signals, and interest-weighted ranking shipped in M1** (`phase-1e`/`1d`) — M3 now *adds* the voice layer + follow on top. The M3 schema addition (`follows`) FKs to M1's `users`/`stories`. Re-scoped 2026-05-30 (see Decision #8 + the M1 phase list; voice-agent onboarding dropped — `onboarding_conversations` removed from the schema).
- [Phase 3](phase-3-m3-auth-voice-foundation.md) — **Auth + user-side schema already shipped in M1 (`phase-1e`)** → reduces to the parameterized Gemini Live transport + shared orb/waveform UI (the rails in-news Voice mode (3b) uses).
- [Phase 3b](phase-3b-m3-in-news-voice-mode.md) — Swipe-left in-news Voice mode, grounded on the single story's sources (in-context, reusing the Phase-2b corpus loader + verification — no vector DB), refusal contract + voice signal.
- [Phase 3d](phase-3d-m3-personalization-follow.md) — **Ranking already shipped in M1 (`phase-1d` → `daily_feeds`)** → reduces to follow-a-story + "what's new since you last watched".

### M4 — App Store ship
- _Not yet planned._

### M5 — Two-axis personalization (sources + control surface)
> Feature list + TLDW reuse map: `plans/m5-m6-personalization-sources-control-surface.md`. Phase files generated 2026-06-05 (`/plan-phases`); each has 4 sub-phases + a 3-lens self-critique.
- [Phase 5](phase-5-recursive-interest-picker.md) — Recursive interest picker (topics axis): port `interest_picker.html` → React + entity registry (migration 0007) + topic/entity follows → ranking. *(Supersedes `phase-1e` chip onboarding.)*
- [Phase 5b](phase-5b-source-data-model-catalog.md) — Source data model (`content_sources`/`personalities`/`archetypes`, migration 0008) + per-archetype catalog seed (port TLDW `seed_catalog`).
- [Phase 5c](phase-5c-source-onboarding-recommendations.md) — Archetype mapping + 3 recommendation screens (YouTube / X+personalities / podcasts): avatar+name+follow+search-add (port TLDW UI; build-fresh X resolver via the worker).
- [Phase 5d](phase-5d-source-ingestion.md) — Ingestion of followed sources into the 30-story pool (port YouTube/podcast adapters + cadence + v4 cron; build-fresh X adapter).
- [Phase 5e](phase-5e-control-surface.md) — Control surface: master dial + per-source priority + 30-cell ribbon (live preview) + presets + pinned-sources-fill-first allocation (migration 0009).
- _Suggested order: **Phase 5 ∥ Phase 5b** (independent), then **Phase 5c ∥ Phase 5d** (both need 5b), then **Phase 5e** (needs 5b + 5d). Static-export reality: registry/recommendation reads are client-side Supabase; external-API search runs on the FastAPI worker; ingestion/agents are server-side._

### M6 — Discovery agent & learned ordering
- [Phase 6](phase-6-discovery-research-agent.md) — Community-signal research agent (build-fresh; crawl→extract→resolve→idempotent upsert into `content_sources`/`personalities`; v4 cron). *(needs Phase 5b.)*
- [Phase 6b](phase-6b-learned-ordering.md) — Learned ordering from watch-completion + questions + follow/unfollow (**not** gestures — C2); applied after pinned-first allocation; cold-start fallback + diversity floor. *(needs Phase 5e.)*

## Riskiest assumption (from brief) and how we test it

**Digest quality** — static images + two-narrator audio may feel cheap/boring vs. slick short-form, the "good-enough-to-be-dull" middle that kills retention. **Tested at M0 before anything else is built:** produce ~5 genuinely finished digests using the real pipeline components (reused TLDW TTS + Remotion compositing), put them in front of target-persona strangers, and measure whether they'd watch ~10 consecutively. If it fails, we fix the format (pacing, voices, motion, caption style) before building M1–M4 on top of it.

## Out of scope

- ~~YouTube channel + podcast ingestion~~ **moved into M5** (2026-06-04) — now in scope as the sources axis, along with **X/Twitter ingestion** (see `plans/m5-m6-personalization-sources-control-surface.md`). News-only remains the scope **through M4 (App Store ship)**.
- Per-outlet factuality ratings, ownership breakdown, geographic spread, entity tagging, related-story threading (deferred per brief — each needs licensed/built data).
- Android, multi-language expansion, payments/subscriptions.
- Per-user video generation; AI-generated video; talking-head avatars.
- Multi-signal behavioral **ML** personalization (M1–M4 use heuristic category prioritization; M5/M6 add the two-axis topics⨉sources control surface + engagement-learned ordering, still heuristic — **not** ML).

## Open questions for /plan-phases

1. **Moat vs. metric (brief Open Q2):** the 3x/week habit is driven by the *passive* reel, but the differentiator is the *active* interrogation layer. Decide which the early phases optimize and instrument.
2. **"World" vs. "my field" (brief Open Q3):** general FOMO wants broad coverage; personalization narrows to a field. Resolve the feed-composition rule (e.g. a guaranteed "top world stories" tier + personalized tier) before building ranking.
3. **M0 placement:** is the quality spike a formal phase, or a pre-phase manual milestone with a go/no-go gate? Recommend treating it as a gate before Phase 1.
4. **Capacitor + Next.js export mode:** static export (SPA) vs. a hosted Next server the shell points at — decide before scaffolding, since it dictates how API routes vs. Supabase-direct calls are split.
5. **Remotion render runtime:** where the Node render executes (the FastAPI worker, a dedicated Remotion Lambda, or a Trigger.dev task) and how it hands off from the Python pipeline (the TLDW `tts_handoff` stage is the template).
6. **Voice mode timing (brief Open Q6):** voice is must-have but sequenced last (M3); decide whether to pull a thin voice slice earlier to de-risk the most novel feature.
7. **Live client-render vs pre-rendered MP4 for the reel (2026-05-29 pivot):** confirm the reel composites live client-side (audio + word-timed caption JSON + ambient poster wash) and decide **where word-timestamps come from** — forced alignment at pipeline time (e.g. the TLDW `align` stage producing the caption JSON) vs. timestamps emitted by the TTS engine — and what JSON schema the client consumes. Also: does Remotion share-export stay in scope for M1, or defer?
8. **Hierarchical interest taxonomy (2026-05-29 pivot):** design the category → subcategory → sub-subcategory tree that backs the onboarding chips and niche-down drill-down (and the feed's interest match). Where does it live (static seed vs Supabase table) and how deep does it go? *(Resolved: a Supabase `interests` self-FK tree, 3 levels, seeded in `phase-1e`; see `reference/supabase-schema.md` §3.)*
