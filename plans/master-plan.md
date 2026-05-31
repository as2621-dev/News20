# Master Plan

**Date:** 2026-05-28
**Source brief:** documents/product-brief.md
**Status:** Active

## Design alignment (2026-05-29)

A design prototype handed off from Claude Design (vendored at `prototype/`) re-directed four things this plan must reflect:

1. **Brand:** the product is now **blip** (an audio/radar "signal" — fits an audio-first app); the codename/repo stays **News20**.
2. **Audio-first reel:** the audio digest is the hero. **Karaoke Playfair captions** (sentence dim → current word white → exactly one `#FACC15` yellow keyword/sentence) sync to the TTS audio; the poster image recedes to a blurred, drifting, segment-accent ambient wash.
3. **Onboarding:** a **voice-agent interest interview** (listening orb + hierarchical tappable category chips + niche-down follow-ups, wired to Gemini Live) replaces the checkbox grid.
4. **Auth:** **email-only passwordless magic-link** (Supabase email OTP); Sign-in-with-Apple removed (avoids the review/entitlement overhead; re-add only if App Store review demands it).

Source of truth: `prototype/` (runnable prototype + `ui-design-decisions.md` + `chats/`), plus `reference/supabase-schema.md` and `reference/prototype-port-map.md` (both being written now). This pivot revises Key design decisions #3 and #7 below — see those entries.

## Vision (one paragraph)

**blip** (codename/repo News20) is a swipeable, auto-playing iPhone news app for the 25–34 commuter who doom-scrolls but feels guilty they're not keeping up. It turns each day's stories into ~55-second AI "anchor-duo" digests (two consistent Gemini TTS voices over still images with motion, sound-off captions) that auto-advance hands-free — ~30 stories in ~30 minutes and you're caught up. The defensible moat is the **interrogation layer**: swipe right into a Story Detail View with a readable chunked text version, supporting visuals, a trust layer (coverage breakdown / blindspot / opposing view), and a search box to *ask the story questions* grounded in the source; swipe left for the same conversation hands-free by voice. It wins against the "do-nothing scroll reflex" by being a finite, completable loop, and against the TikTok FYP by letting you interrogate a story instead of hitting a dead-end clip.

## Tech stack

- **Frontend:** Next.js 15 (React 19) + Tailwind 4 + radix-ui + framer-motion, built as a static SPA and wrapped in **Capacitor** for a native iPhone App Store binary. Rationale: keeps near-total reuse of TLDW's web UI scaffolding while delivering an App-Store-distributable native shell with mic + native video access. *(Flagged for revisit per Rule 7: Expo/React Native would feel more native but forfeits TLDW frontend reuse — chosen against for v1.)*
- **Backend:** Supabase (Postgres + auth + storage/CDN). Rationale: relational story/source/signal modeling + file storage for rendered MP4s; already the TLDW backend, so schema patterns and client setup port directly.
- **Agents:** Python agents (Pydantic-typed), reused wholesale from TLDW. Rationale: the TTS, RAG-grounded Q&A, scripting, and ingestion logic already exist and are the expensive parts — see `reference/reuse-map.md`.
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
      │                                    │   • rag: chunk·embed·retrieve      │
      └───────────────┬────────────────────┴───────────────┬───────────────────┘
                      │                                     │
            ┌─────────▼──────────┐               ┌──────────▼─────────┐
            │ External APIs       │               │ Pinecone (RAG)     │
            │ NewsAPI·MediaStack· │               │ grounds Q&A + voice│
            │ AlphaVantage·HN·PH  │               │ on the source text │
            │ Pexels·Unsplash·    │               └────────────────────┘
            │ Pixabay (images)    │
            │ Gemini TTS + Live   │
            │ AllSides/AdFontes   │
            │  (static bias map)  │
            └─────────────────────┘
```

## Key design decisions

1. **Capacitor wrapping a Next.js static SPA** for the iPhone app. Why: App-Store-native distribution + native mic/video without rewriting the React UI in RN. Rules out: a separate Expo/React Native frontend (and its loss of TLDW reuse).
2. **Inherit the TLDW stack wholesale** (Supabase + Trigger.dev v4 + Pinecone + Python agents). Why: the costly pieces (multi-speaker TTS, grounded Q&A, ingestion) already exist and are battle-tested. Rules out: Convex, a fresh backend, LangChain.
3. **One canonical audio digest + word-timed caption JSON + poster image per story, served to many users by interest match** — never per-user generation. Why: unit economics; the brief's core viability constraint. *(Revised 2026-05-29 per the audio-first pivot: the shipping reel is no longer a pre-rendered 9:16 MP4 — it composites **live in the client** from the audio + word-timestamped caption JSON + an ambient poster wash. Same one-canonical-asset-per-story unit economics; the asset is now a small audio+JSON+image bundle, not a video file.)* *(Clarified 2026-05-30 re-scope: "served to many users by interest match" is implemented via a **deduped story pool + `story_interests` fan-out** — a story is produced **once** and shared with every user whose interest it serves; a user with a genuinely unique interest may be the only consumer of a given story, but it is still one canonical asset per story, never the same story re-generated per user.)* Rules out: per-user re-generation of the same story.
4. **Digest scripts are constrained to a single source article.** Why: hallucination control in the generated narration. Rules out: multi-source synthesis in the script stage.
5. **Interrogation (typed + voice) is RAG-grounded + passes a verification stage.** Why: news has zero tolerance for invented facts (brief Open Q5); answers must cite the source, and the model must refuse when the source can't support an answer. Rules out: free-form ungrounded LLM answers in Q&A/voice.
6. **The entire trust layer derives from one static outlet→bias lookup table** (AllSides / Ad Fontes). Why: coverage breakdown, blindspot flag, and "opposing view" all fall out of it at near-zero per-story cost. Rules out: licensed per-outlet factuality / ownership data.
7. **Remotion DEMOTED to OPTIONAL share-export / social clips — NOT the in-app reel.** *(Revised 2026-05-29 per the audio-first pivot.)* The in-app reel composites **live in the client** (TTS audio + word-timed caption karaoke + an ambient drifting poster wash); it is **not** a Remotion-rendered MP4. Remotion (still images + Ken Burns, no AI video, no avatars) is retained only for an optional out-of-app 9:16 MP4 used for sharing / social clips. Why client-live for the reel: the audio-first format wants per-word caption timing and a calm ambient wash that the client can drive directly, and it removes per-story video render cost/latency from the critical path. **⚠️ CONFLICT FLAGGED (Rule 7/12):** this contradicts the prior framing where Remotion rendered the canonical in-app reel and the M0/Phase-0 work is described as "render 5 MP4s." Resolution: client-live wins for the *shipping* reel (it's the more recent, owner-directed, audio-first decision); Remotion is kept but demoted to share-export. **M0's MP4 render still has value as a *content-quality* test** (does the script/voice/pacing read well?) — so **do NOT delete Phase 0 or its progress file**; treat M0 as a format/quality gate, not the production renderer. **`/plan-phases` should re-plan M1 and M3 against the new reference docs** (`reference/prototype-port-map.md`, `reference/supabase-schema.md`) before either is built. Rules out: Sora/Veo/Runway video, HeyGen/Synthesia avatars, and a pre-rendered MP4 as the in-app reel.
8. **Personalization = category prioritization from engagement signals** (reuse TLDW `memory/player_signals`), not an ML model. Why: simplicity for v1; surface the categories the user engages with first. Rules out: the 6-signal behavioral ML described in the report. *(Re-scoped 2026-05-30: personalization is now **M1, not M3**, and is interest-keyed per `reference/ranking-spec.md` — a heuristic per-(user,story) Score + a ~30-slot per-user allocation precomputed into `daily_feeds`, with a bounded/decayed signal→weight loop. Still no ML.)*
9. **A quality spike (M0) gates the full build.** Why: the brief's riskiest assumption is that static-images + two-narrator digests feel too cheap to retain. Produce ~5 real digests, test on strangers, before committing to the full pipeline. Rules out: building all of M1–M4 on an unvalidated format.

## Milestones (not phases — phases come from /plan-phases)

- **M0 — Quality spike (de-risk gate):** ~5 real end-to-end digests produced (news article → script → anchor-duo TTS → images + Ken Burns + captions → MP4) and shown to strangers. *True when:* we have evidence people would watch 10 in a row. This validates the riskiest assumption before scaling.
- **M1 — Personalized audio-first karaoke reel MVP:** Email magic-link auth + chip-based 3-level interest onboarding → an **interest-keyed** once-per-story pipeline that ingests news per the union of users' interests into a **deduped story pool** and produces the canonical **audio digest + word-timed caption JSON + ambient poster image** in Supabase → a **per-user feed** (`daily_feeds`, scored + allocated per `reference/ranking-spec.md`) → Next.js swipe-up reel that **composites karaoke captions live in the client** (sentence dim → current word white → one `#FACC15` keyword/sentence) synced to the TTS audio over a drifting poster wash, running inside a Capacitor iOS build. *True when:* a new user can sign in, pick interests, and passively listen-and-read ~20–30 fresh daily stories **chosen for them** back-to-back, captions tracking the audio word-by-word. *(Re-scoped 2026-05-30: personalization + email auth + chip onboarding pulled forward from M3 — the app is never shipped anonymous; voice onboarding stays in M3.)*
- **M2 — Detail View + trust + interrogation:** Swipe-right Story Detail (chunked readable text <100s, supporting visuals), the bias/coverage/blindspot/opposing-view trust layer, and the typed search-box Q&A grounded via RAG. *True when:* a user can read, see who's covering a story, and ask it a question that's answered from the source.
- **M3 — Voice mode + voice-onboarding + follow:** Swipe-left Gemini Live voice mode, a **voice-agent interest interview layered on M1's chip onboarding** (the same Gemini Live brain as Voice mode, writing the *same* `user_interest_profile`), and follow-a-story + "what's new since you last watched". *True when:* a user can refine their interest profile by voice and interrogate a story hands-free. *(Re-scoped 2026-05-30: email auth, chip onboarding, the interest profile, engagement-signal instrumentation, and interest-weighted ranking all moved to M1 — M3 now adds the voice layer + follow on top.)*
- **M4 — App Store ship:** TestFlight build, polish, accuracy/guardrail review, App Store submission. *True when:* the app is live and the 3x/week metric is instrumented.

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
- [Phase 2b](phase-2b-m2-grounded-interrogation.md) — Grounded typed Q&A: ported TLDW RAG retriever + verification stage → citation-chip answers / `⌀ CAN'T ANSWER FROM SOURCE` refusal, persisted/cached to `story_qa` (the shared brain M3's voice reuses).
- _⚠ Both assume M1 foundations (Next scaffold, Supabase client + migrated/seeded content tables, LayerStack/reel, tokens) — **M1 is now planned** (`phase-1`/`1b`/`1c`/`1d`). Build M1 first._ Numbered `phase-2`/`phase-2b` (one integer per milestone, mirroring M0's `phase-0`/`phase-0b`) to avoid colliding with M1's `phase-1` group.

### M3 — Voice mode + voice-onboarding + follow (auth · chip onboarding · ranking now in M1)
> ⚠ **Depends on M1 (`phase-1`/`1b`/`1e`/`1d`/`1c`) + M2 (planned, not built).** M3's voice mode reuses M2's RAG endpoint (`phase-2b`); **auth, the user-side schema, chip onboarding, the interest profile, engagement signals, and interest-weighted ranking shipped in M1** (`phase-1e`/`1d`) — M3 now *adds* the voice layer + follow on top. The M3 schema additions (`onboarding_conversations`, `follows`) FK to M1's `users`/`stories`. Re-scoped 2026-05-30 (see Decision #8 + the M1 phase list).
- [Phase 3](phase-3-m3-auth-voice-foundation.md) — **Auth + user-side schema already shipped in M1 (`phase-1e`)** → reduces to the parameterized Gemini Live transport + shared orb/waveform UI (the rails 3b/3c share).
- [Phase 3b](phase-3b-m3-in-news-voice-mode.md) — Swipe-left in-news Voice mode, RAG-grounded on the single story's sources, refusal contract + voice signal.
- [Phase 3c](phase-3c-m3-voice-onboarding.md) — Voice-agent interest interview **layered on M1's chip onboarding** (same `user_interest_profile` upsert path; adds the Gemini Live orb + function-calling extraction + `onboarding_conversations`). The chip UI itself shipped in `phase-1e`.
- [Phase 3d](phase-3d-m3-personalization-follow.md) — **Ranking already shipped in M1 (`phase-1d` → `daily_feeds`)** → reduces to follow-a-story + "what's new since you last watched".

### M4 — App Store ship
- _Not yet planned._

## Riskiest assumption (from brief) and how we test it

**Digest quality** — static images + two-narrator audio may feel cheap/boring vs. slick short-form, the "good-enough-to-be-dull" middle that kills retention. **Tested at M0 before anything else is built:** produce ~5 genuinely finished digests using the real pipeline components (reused TLDW TTS + Remotion compositing), put them in front of target-persona strangers, and measure whether they'd watch ~10 consecutively. If it fails, we fix the format (pacing, voices, motion, caption style) before building M1–M4 on top of it.

## Out of scope

- YouTube channel + podcast ingestion (explicit later phase per brief; news-only for v1).
- Per-outlet factuality ratings, ownership breakdown, geographic spread, entity tagging, related-story threading (deferred per brief — each needs licensed/built data).
- Android, multi-language expansion, payments/subscriptions.
- Per-user video generation; AI-generated video; talking-head avatars.
- Multi-signal behavioral ML personalization (v1 uses simple category prioritization).

## Open questions for /plan-phases

1. **Moat vs. metric (brief Open Q2):** the 3x/week habit is driven by the *passive* reel, but the differentiator is the *active* interrogation layer. Decide which the early phases optimize and instrument.
2. **"World" vs. "my field" (brief Open Q3):** general FOMO wants broad coverage; personalization narrows to a field. Resolve the feed-composition rule (e.g. a guaranteed "top world stories" tier + personalized tier) before building ranking.
3. **M0 placement:** is the quality spike a formal phase, or a pre-phase manual milestone with a go/no-go gate? Recommend treating it as a gate before Phase 1.
4. **Capacitor + Next.js export mode:** static export (SPA) vs. a hosted Next server the shell points at — decide before scaffolding, since it dictates how API routes vs. Supabase-direct calls are split.
5. **Remotion render runtime:** where the Node render executes (the FastAPI worker, a dedicated Remotion Lambda, or a Trigger.dev task) and how it hands off from the Python pipeline (the TLDW `tts_handoff` stage is the template).
6. **Voice mode timing (brief Open Q6):** voice is must-have but sequenced last (M3); decide whether to pull a thin voice slice earlier to de-risk the most novel feature.
7. **Live client-render vs pre-rendered MP4 for the reel (2026-05-29 pivot):** confirm the reel composites live client-side (audio + word-timed caption JSON + ambient poster wash) and decide **where word-timestamps come from** — forced alignment at pipeline time (e.g. the TLDW `align` stage producing the caption JSON) vs. timestamps emitted by the TTS engine — and what JSON schema the client consumes. Also: does Remotion share-export stay in scope for M1, or defer?
8. **Hierarchical interest taxonomy (2026-05-29 pivot):** design the category → subcategory → sub-subcategory tree that backs the onboarding chips and niche-down follow-ups (and the feed's interest match). Where does it live (static seed vs Supabase table), how deep does it go, and how does the voice transcript map onto it?
9. **Voice onboarding as a Gemini Live agent on the shared Voice stack (2026-05-29 pivot):** the onboarding interview should reuse the same Gemini Live agent as Voice mode, extracting an interest profile from the transcript via function-calling (not the prototype's canned script). Decide the function-calling contract for "interest detected", how it writes the interest profile, and whether this pulls a thin voice slice ahead of M3.
