# Personalization & Source Curation — Feature Spec

> Scope: onboarding, profile mapping, source recommendation, and the settings/preferences control surface that lets a user balance **topic-driven discovery** against **source-driven follows** across the fixed 30-story digest.

---

## 1. Context & Goal

The app converts news into a TikTok-style swipeable feed of short AI-generated video digests. The digest is a **fixed window of 30 stories**.

This feature extends the app beyond mainstream news into a **personal news-gathering service**. The insight: for niche interests (AI, AI chips, etc.), the real "news" increasingly happens on **YouTube** (interviews, podcasts) and **Twitter/X** (lab researchers, analysts), not on traditional outlets. Staying current means following the *right people*, which is otherwise a multi-hour daily effort (the "Gavin Baker problem" — needing ~6 hrs/day of podcasts/video to keep up).

So the system must let a user feed their digest from **two axes**:

- **Topics** — *what* a story is about (geopolitics, AI chips, sports…)
- **Sources** — *who* a story comes from (a YouTube channel, a Twitter handle, a named personality)

These two axes overlap (one channel spans many topics; one topic spans many sources). The design challenge is letting users prioritize across both without conflating them.

---

## 2. Onboarding Flow

### 2.1 Sub-niche selection (drill-down)

A progressive 3-step (occasionally 4-step) category drill-down:

1. **Top-level categories** (e.g. Tech, Markets, Sports, Geopolitics, Entertainment…)
2. **Subcategories** within each selected top-level category
3. **Sub-subcategories** for granularity
4. **(Optional) one more layer** where needed to get truly granular (e.g. Tech → AI → AI Chips)

The output of this flow is a structured interest profile for the user.

### 2.2 Profile mapping

- Pre-define a set of **10–15 named archetype profiles** in the background (e.g. "Tech + Markets", "Bollywood + Fitness"). Profiles are **named**.
- After the user completes the drill-down, **map the user to the closest archetype profile** based on their selections.
- Each archetype profile carries pre-computed source recommendations (see §3). This makes recommendations instant rather than computed per-user at onboarding time.

> Implementation note: mapping can be a similarity match between the user's selected category vector and each archetype's category vector. Profiles can be refined/added over time without changing the onboarding UI.

---

## 3. Source Recommendation Flow

After profile mapping, walk the user through **three recommendation screens**, in order. Each pulls from the archetype profile's pre-computed lists, optionally re-ranked by the user's specific sub-niche selections (e.g. a heavy "AI chips" user gets AI-chips-weighted sources).

1. **YouTube channels** — "People like you also follow these channels. When fresh content is posted, we'll surface it." Show ~10–20 channels.
2. **Twitter/X handles** — "These are the handles these people follow. Want to follow them?"
3. **Personalities** — famous/authoritative people in the space.

### 3.1 UI requirements per recommended entity

- **Thumbnail/avatar** (required for every YouTube channel, Twitter handle, and personality)
- **Display name** (required)
- Select/follow toggle
- A **search + add** affordance on every screen so the user can add anyone not in the recommended list.

### 3.2 Populating & refreshing the recommendation lists

- Use an **LLM query** keyed on the profile (or pre-mapped per profile in advance) to produce the candidate source lists for each niche.
- Run a **background research agent** to seed and refresh these mappings: crawl community signals (Reddit threads, Twitter conversations, podcast directories, industry forums) to identify who each niche actually recommends.
- The agent runs **periodically** to catch newly rising voices, keeping mappings fresh without manual curation.

---

## 4. Ingestion (downstream of follows)

For each followed source:

- **YouTube channels** — detect fresh uploads; scrape/transcribe and extract the substance (e.g. an important interview or broadcast) for digest conversion.
- **Twitter/X handles** — monitor posts from followed accounts.
- These flow into the same 30-story digest pipeline as topic-driven mainstream news.

---

## 5. Settings & Preferences — the Control Surface

This is where the user balances the two axes across the 30 story slots. The core design principle: **don't make topics and sources fight over one slider.** Separate them, then resolve conflicts with one rule.

### 5.1 Master dial (top of page)

A single control: **My Sources ←→ Discovery**.

- Decides how many of the 30 stories come from **followed sources** vs **topic-based coverage**.
- Full left → all 30 are fresh drops from followed channels/handles ("make it all my stuff").
- Full right → pure topic discovery.
- Most users sit in the middle.

This one dial resolves the primary tug-of-war intuitively.

### 5.2 Followed Sources list

Below the dial: the user's followed sources as a list. Each row:

- Avatar + name
- Per-source **priority** setting: `Off` · `Only their big stuff` · `Everything they post`

### 5.3 Topic ribbon (allocation)

The draggable **30-cell color-coded ribbon** (existing design). Each top-level topic is a color (e.g. geopolitics = purple, sports = another color, AI chips = another). The user drags boundaries to rebalance attention across topics.

Important: the topic ribbon allocates only the slots **left over** after followed sources claim theirs (see §5.5).

### 5.4 Presets (for users who don't want to fiddle)

Three presets that set the master dial + sane defaults:

- **Power Feed** — mostly sources
- **Balanced**
- **Wide Lens** — mostly discovery

Manual controls remain available beneath the presets for anyone who wants to go deeper.

### 5.5 Conflict-resolution rule (kills the overlap problem)

**Pinned sources fill first; topics fill the rest.**

- If a followed source (e.g. Dwarkesh) drops new content, that slot is already claimed by the source.
- The topic budget (e.g. "AI") then fills only the remaining gaps with mainstream coverage.
- No double-counting; no ambiguity about which bucket a story lands in.

### 5.6 Live preview (the key UX detail)

The 30-cell ribbon is a **live preview**, not a static control. As the user drags *anything* — master dial, a source priority, a topic boundary — the ribbon re-renders instantly:

- **Source-driven cells** show a tiny avatar.
- **Topic cells** show solid color.

The user sees the consequence of every adjustment in real time, so allocation stops feeling abstract.

---

## 6. Ordering & Learning (phase 2)

- **v1:** user manually sets the topic/source mix and ordering.
- **Then:** let implicit signals refine ordering over time — which topic/source the user wants at the top of the feed is learned, not fixed.
- Signals already being captured: **watch completion, gesture usage, questions asked, follow/unfollow**. Use these to learn the best ordering per user (e.g. surfacing AI-chips stories first because that's where engagement concentrates).

> Don't hard-code order (e.g. "geopolitics is always first 4"). Start manual, then adapt to engagement.

---

## 7. Open Questions / TODO

- Exact archetype profile set (the 10–15) and their category vectors.
- Re-rank logic: how strongly sub-niche selections re-weight an archetype's default source lists.
- Research agent: source list, crawl cadence, dedupe/ranking of community signals into a recommendation list.
- How `Only their big stuff` is determined per source (engagement threshold? duration? topic match?).
- Whether the master dial is a hard split or a soft bias when source content is sparse on a given day.

---

## 8. Carried-over locked decisions (for reference)

- Fixed **30-story** digest window.
- Video format: still images + Ken Burns motion, animated word-by-word captions (sound-off first), two-speaker podcast-style audio (Gemini 2.5 Flash multi-speaker TTS, NotebookLM-style).
- Five-segment ~55s structure: Hook → Stakes → Detail → Why It Matters → CTA/Loop.
- Gestures = navigation intent, **not** preference signal (swipe up = next, right = Story Detail, left = voice, tap headline = lightweight overlay).
- Longer formats invest more time *per story* rather than adding more stories.
