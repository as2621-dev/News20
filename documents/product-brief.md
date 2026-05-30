# Product Brief

**Date:** 2026-05-28
**Status:** Draft — needs `/cto` to translate into a plan

## One-liner

A swipeable, auto-playing news app that turns the day's stories into 55-second AI digests you can interrogate.

## Target user

25–34, ~40-minute daily commute. Currently doom-scrolls Instagram/TikTok Reels but feels guilty they're "not keeping up." The moment they reach for this: the morning commute, when they have dead time and a low-grade fear of showing up uninformed.

## Problem

The world moves too fast and they have no time to keep up. The result is social FOMO — they either miss a story entirely or have only vague awareness of it ("something happened at the Google event, I don't know what"), and they feel dumb when it comes up around peers. The pain is *social*, not intellectual curiosity.

## Today's workaround

They scroll Instagram/TikTok and hope the algorithm surfaces what matters; when they hear a story referenced, they react late by Googling it, bouncing between scattered sources, and losing the thread. Mostly they just stay behind and accept it. Insufficient because passive feeds hand out fragments, never understanding — and chasing a story across tabs is so much friction they give up.

## Unique angle

**Interrogate any story in place — no context-switching.** Today: watch a clip → leave to Google → read scattered sources → try to find your way back, and get lost. Here: you're listening to a digest, and the moment you have a question you ask it (typed or voice), grounded in the source, without ever leaving the story. The clip is a dead end; the digest is a doorway.

Supporting (not the core moat): a **finite "30 stories = caught up" loop** — the opposite of infinite scroll, a thing TikTok structurally can't offer — and a **trust layer** (bias coverage, blindspot flag, opposing view) that signals "we did the research" before showing it to you.

## Smallest provable version (MVP)

Full v1, **sequenced by risk** (not cut). Build and validate top item before moving down:

- **Auto-play vertical reel** of ~20–30 daily stories: anchor-duo audio over static images with motion (pan/zoom/blur), word-by-word captions, hands-free auto-advance. *(Highest-risk, build + audience-test first.)*
- **Swipe-right Story Detail View:** chunked readable text (<100s read), supporting visuals (graph/timeline/images), and a search box at the bottom for typed, source-grounded Q&A.
- **Trust layer:** coverage breakdown (L/C/R), outlet count, story timeline, blindspot flag, "read the opposing view" — all from one cheap outlet→bias lookup table (AllSides/Ad Fontes).
- **Personalization (simple):** interest categories at onboarding + prioritize the categories the user engages with. No multi-signal ML in v1.
- **Voice mode (Gemini Live):** hands-free swipe-left conversation about the current story. *(Most novel + expensive — sequenced last.)*

## 90-day success metric

**3+ play sessions per week per active user** — proves it became a commute habit, not a one-time demo. Leading indicator: % of week-1 signups still opening it at week 4.

## Riskiest assumption

**Digest quality.** Static images + two-narrator audio may feel cheap or boring next to slick human-made short-form — the "good enough to be dull" middle that kills retention. **Test first:** produce ~5 real digests and put them in front of strangers; do they watch 10 in a row? De-risk this before the full build.

## Competition

- **Primary — "do nothing" / the existing scroll reflex.** The real competitor. The persona already has a free, frictionless, dopamine-tuned habit (open IG, scroll, arrive at work). We're asking them to break a reflex, not switch apps. Our wedge: a *finite, completable* loop TikTok can't offer because its business depends on you never finishing.
- **Secondary — TikTok/Instagram For You page.** Already serves news clips for free with better content variety; a user can feel "70% informed" and never download us. Our wedge: you can *question* the story and *see who's covering / hiding it* — the clip is a dead end.
- **Market-validators (not direct threats) — Artifact (shut down), Particle, News Minimalist, Bulletin.** Mostly text feeds; none combine auto-play video + interrogate-in-place + trust layer. They prove the market; they don't own our wedge.
- **Indirect — ChatGPT/Gemini voice, newsletters (Morning Brew), podcasts (The Daily).**

## Open questions

Flagged for `/cto` and `/office-hours`:

1. **Validate quality before full build.** Gate the project on a 5-digest audience test before committing to the full pipeline. (Riskiest assumption.)
2. **Moat vs. metric mismatch.** The 3x/week habit is driven by the *passive* auto-play loop, but the differentiator is the *active* interrogation layer — which most users won't lean into. Is interrogation the real moat, or is the finite "caught up" loop? Resolve what we optimize.
3. **"World" vs. "my field" contradiction.** General FOMO (the Google event) wants broad coverage; personalization ("show my field first") narrows it. A geopolitics-first feed still misses the Google event. Decide: catch up on the world, or on a niche?
4. **Trust layer audience fit.** Bias/blindspot is a power-user feature; the persona is a casual commuter. Cheap to build, but don't expect it to drive the habit. Confirm it's worth detail-view real estate.
5. **Accuracy guardrail.** Scripts are constrained to one source, but open Q&A + voice mode let users push past it ("what else happened?"), inviting hallucination. News has zero tolerance for invented facts. Needs a grounding/refusal strategy in the technical plan.
6. **Differentiator validated last.** Voice mode is must-have but sequenced last, so a core novel feature stays untested for months. Accept, or pull a thin voice slice earlier?
