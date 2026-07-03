# Product Brief — Conversational Micro-Interest Onboarding + Niche-First Feed

**Date:** 2026-07-03
**Status:** Draft — needs `/cto` to translate into a PRD
**Scope:** Revamp of an existing product (blip / News20). Supersedes the tree-picker onboarding (categories → subcategories → sub-subcategories) and the root-keyed Build-My-30 allocation. The original 2026-05-28 whole-product brief is in git history (`3a1da08`).

## One-liner

A chat interview learns your micro-interests, then your 30 daily stories are literally about *them*.

## Target user

A news-curious person with 2-3 genuine sub-niches (e.g. "IPL auction drama", "semiconductor breakthroughs", "AI lab politics") who today scrolls ~10 different sources to stay current on them. The moment: first app open — they'll invest a few minutes personalizing *if* the payoff is visibly their niches, not broad categories.

## Problem

A fixed taxonomy can never contain a person's actual micro-interests. "IPL auction drama" is not a node in any pre-built tree. So users pick the nearest broad category, the profile comes out shallow, and the feed reads like a generic newspaper instead of *their* feed.

## Today's workaround

Users scroll multiple apps/sites/X/YouTube to assemble their niche coverage manually. Inside our product: the tree picker + 8-root allocation, which captures breadth but not depth.

## Unique angle

**The bubbles are generated live, not pre-written.** Each tap generates the next, more specific set of options about what the user just chose (Sport → Cricket → IPL → "auctions/transfers?") — a good waiter inventing suggestions vs. a printed menu. Two free wins fall out:

1. **The drill-down path IS the fallback ladder** — captured at interview time, no extra modeling.
2. **Feed sections are named in the user's own vocabulary** ("Silicon — 3", "IPL — 4") — the visible proof the app heard them.

## Smallest provable version (MVP)

Pieces ①②③ ship together as one chain (they can't be proven apart); ④ is a fast-follow.

1. **① Chat interview** — live-generated bubbles, 2-3 drill-downs per lit-up area, starts from the 8 existing roots, always offers "not really / skip" and "something else — type it". Output: 5-15 named micro-interests + their ladder paths. Cap ~3 min / ~15 taps.
2. **② Niche ingestion** — switch to the GDELT BigQuery adapter (already written, not wired: `agents/ingestion/adapters/gdelt_bigquery.py`; production hard-codes the throttled DOC adapter in `agents/worker/pipeline_routes.py`). One batched query hunts all micro-interests; the slow keyless DOC door cannot serve hundreds of niche queries.
3. **③ Feed reshaping** — Build-My-30 sections become the user's niches; dry days climb the ladder **with an honest label** ("Nothing new in IPL today — here's cricket"); 3-5 slots reserved as a "beyond your bubble" section; followed YouTube/X source slots keep their existing lead positions unchanged.
4. **Validation before polish** — run the full chain on 3 personas: the founder's real profile, a "cricket obsessive", a "chip-industry nerd".

**④ (fast-follow, explicitly deferred):** regenerate source suggestions (YouTube channels, X account clusters, podcasts) per-user from the micro-interest profile — LLM-proposed, code-verified to exist/be active before display, with a thin hand-curated safety-net list per root as filler. No persona-catalog middleman. Until then, the existing M6a cluster/source screen bridges (keyed to old broad categories — slightly mismatched but functional).

## 90-day success metric

Pre-launch, so self-measurable the week it ships:

1. **Niche hit rate ≥ 60%** — per test persona, ≥ ~18 of 30 slots on a typical day are direct micro-niche stories (no fallback label). Below that, the feed reads "cricket app", not "IPL app" — bet #2 failed.
2. **Interview ≤ 3 minutes / ~15 taps** to 5+ named micro-interests. Longer, and bet #1 (tolerance for the interview) is shaky.

## Competition

- **Direct:** Google News / Artifact-style personalized aggregators — personalize by broad topic + click behavior; none extract named micro-interests conversationally or name feed sections in the user's vocabulary.
- **Indirect:** X/YouTube feeds themselves (the user's current 10-source scroll), per-niche newsletters.
- **Do nothing:** keep the tree picker — onboarding stays fast but the feed stays generic; the personalization promise that differentiates blip goes unproven.

## What held up under pressure

- Labeled fallback — turns dry days into proof the app tracks the niche; silent substitution trains users to think personalization is fake.
- Generated + code-verified source suggestions — no stale hand-maintained persona catalog; handles blended users (60% AI + 40% Bollywood fits no bucket).
- Niches-as-sections — the "wow, it heard me" payoff that justifies the interview cost.
- Deferring piece ④ — existing source screen works; don't block the core chain on it.
- BigQuery as the ingestion door — adapter ~90% done; wiring = credentials + one package + a one-line swap.

## What's still soft

1. **"Users will spend 3-5 minutes personalizing" — assumption, zero observation.** Founder conviction only. Watch interview completion/abandonment from day one.
2. **60% niche hit rate is a guess** until BigQuery proves real niche coverage. Some niches may be genuinely dry most days.
3. **Live bubble generation quality is unproven** — bad second-level bubbles (too generic, hallucinated, irrelevant) silently degrade the whole profile.
4. No evidence yet that the tree picker produces a bad feed — the pain is inferred from taxonomy logic, not watched user behavior.

## Riskiest assumption

**That micro-niche ingestion can actually fill the feed** — i.e. GDELT/BigQuery surfaces enough direct-niche stories that the labeled fallback is the exception, not the rule. If most days most sections show "nothing new in your niche", the interview over-promised and the revamp reads as a downgrade. Test first, with the 3 personas, before any UI polish.

## Contradictions surfaced

1. **"Open-ended interview" vs. pre-written bubble examples** — the founder's sketch ("Do you follow sports?" → fixed bubbles) was the tree picker in chat clothing. Resolved: bubbles must be generated live from the previous answer; the fixed-bubble version is explicitly rejected as cosmetic.
2. **Cookie-based YouTube/X subscription import vs. App Store reality** — technically possible, but violates YouTube/X ToS and Apple guideline 5.2.2 (rejection + takedown risk). Resolved: **killed entirely**, including the OAuth alternative (founder's call) — persona-based *suggestions* stay load-bearing instead.

## Open questions

1. Exact "beyond your bubble" slot count (3-5) and how those stories are chosen.
2. What powers live bubble generation and its latency/cost budget per interview (technical — for `/cto`).
3. BigQuery wiring details: credentials, billing project, `google-cloud-bigquery` dependency (for `/cto`).
4. How existing users / test profiles migrate from tree-picked interests to interview-derived micro-interests.
5. Where the interview profile is stored and how re-runs work ("re-interview me").
