# UI Design Brief — News20 (for a design pass)

**You are designing the entire UI for News20 as a runnable, interactive HTML + Tailwind prototype.**
This is not production code — it's a clickable, swipeable, gesture-faithful prototype that proves the
look, feel, and flow, and that ports cleanly into the real Next.js 15 + Tailwind 4 build later.

You have access to this repo. **Read the canonical docs in §2 before designing** — they are the source of
truth. This brief tells you *what to build and to what bar*; those docs tell you the *exact tokens, poster
rules, and product reasoning*. Where they conflict with this brief, the docs win on tokens/poster rules;
this brief wins on deliverable shape and scope.

---

## 1. The product in one paragraph (so every pixel serves it)

News20 is a swipeable, auto-playing iPhone news app for the 25–34 commuter who doom-scrolls but feels
guilty they're "not keeping up." Each day's stories become ~55-second AI "anchor-duo" digests — two voices
over a single poster-grade image, with sound-off word-by-word captions — that **auto-advance hands-free**:
~30 stories in ~30 minutes and you're *caught up*. The product's soul is the **anti-doom-scroll, finite,
completable loop** (TikTok structurally can't offer a finish line). Its moat is **interrogate-in-place**:
swipe right into a readable Story Detail with a **trust layer** (who's covering it, bias breakdown,
blindspot flag, opposing view) and a box to **ask the story questions grounded in its source**; swipe left
for the same conversation hands-free by voice. The clip is a dead end; the digest is a doorway.

**North-star feel:** serious but not stiff — informative, credible, calm. A finite briefing you *finish*,
not an infinite feed you fall into. Trust is expressed *through design*, not claimed in copy. Never let it
read as entertainment.

---

## 2. Read these first (canonical — do not re-derive)

| Doc | Take from it |
|---|---|
| `reference/design-language.md` | **The token system** — colors, type (Inter / Playfair Display / JetBrains Mono), spacing, radius, motion, voice/tone. This is the visual law. |
| `reference/poster-pipeline.md` | The **story image system** + the **fixed overlay frame** (our brand "frame," like TIME's red border). §2 frame, §3 archetypes, §6 house grade, §7 scrim + safe zones, §8 segment accents. |
| `documents/product-brief.md` | Who the user is, the moat, the tone, the riskiest assumption. |
| `plans/master-plan.md` | The surfaces, the interaction model (swipe up/down/right/left), and the locked product decisions. |
| `reference/stack-notes.md` | **iOS/Capacitor realities** the prototype must respect — safe-area insets, muted-autoplay + first-tap-to-unlock-audio, `playsinline`, video preloading, mic permission. Build the prototype to *feel* like the real constrained shell. |

If a token, accent, or rule appears in those docs, use it verbatim. Don't invent a parallel system.

---

## 3. What to produce (deliverable)

**A single runnable prototype, no build step.**

- **Stack:** plain HTML + vanilla JS + **Tailwind via the Play CDN** (`<script src="cdn.tailwindcss.com">`)
  with an inline `tailwind.config` that maps the design-language tokens to theme keys (so class names like
  `bg-background`, `text-caption-highlight`, `font-serif` are real and port to the Next.js build).
  Fonts via Google Fonts (Inter, Playfair Display, JetBrains Mono). No React, no bundler, no npm.
- **Entry point:** `index.html` — the full gesture flow (reel → swipe right → detail → swipe left → voice,
  plus onboarding, follow, and all states), all reachable. Split shared JS/CSS into a few files if it helps,
  but it must run by either opening `index.html` directly or `python3 -m http.server` in the folder.
- **Render inside an iPhone frame.** Target a 393×852 logical viewport (iPhone 15-class) centered on a
  neutral page. Simulate safe areas with fixed insets (≈59px top for the Dynamic Island, ≈34px bottom home
  indicator) — content must respect them. It should be obvious this is a phone app, and it must look right
  at that size in browser device mode.
- **Mock everything.** No backend, no real APIs, no real image generation. Hardcode the sample stories in
  §7. **Mock the poster images** with CSS (near-black `#020617` duotone gradients + one accent + a simple
  silhouette/shape in the upper third, dark lower 40%) or 3–4 representative placeholder images that obey
  the poster §6 grade. The poster art is *not* your deliverable — **the overlay frame on top of it is.**
- **Also write `reference/ui-design-decisions.md`** — a short companion logging the calls you made, the
  judgment calls from §9 and how you resolved each, and anything you'd want the engineering team to know
  when porting. This composes your work back into the repo.

Fidelity bar: production-grade *visual* fidelity and *real* motion (not screenshots). Every surface and
state in §5 reachable. Behaves on a touch device and with a mouse.

---

## 4. Non-negotiables (cheat-sheet — full detail in the docs)

- **Canvas is near-black `#020617`.** Dark everywhere. Light `surface #D1D4BD` only for detail-view reading
  cards, used sparingly.
- **Type:** Inter = display/headlines, **Playfair Display = readable body** (the detail-view prestige
  move), **JetBrains Mono = all trust stats / metadata / timestamps** (the "we did the research" signal).
- **One accent per poster**, segment-coded (geopolitics ember `#EF4444`, markets green `#22C55E`, tech cyan
  `#22D3EE`, sport amber `#F59E0B`, wildcard blush `#E8B7BC`). Accents must never collide with the
  UI-reserved `#3B82F6` (actions/active) or `#FACC15` (caption highlight). These accents are *provisional*
  in the docs — if one fails contrast on `#020617`, flag it in the decisions file.
- **The fixed overlay frame is the #1 brand lever.** Logo zone (top), headline zone (lower third over a
  scrim), action row, bottom scrim gradient — *identical on every story*; only the image beneath varies.
  Never bake headline text into the image.
- **Captions (sound-off, first-class):** bold white, black outline, lower-middle third, **word-by-word
  reveal**, exactly **one `#FACC15` highlighted keyword per sentence.** Comprehension with audio off is a
  requirement, not a nice-to-have.
- **Gestures:** swipe **up/down** = next/prev story; swipe **right** = Story Detail; swipe **left** = voice
  mode; **tap** = pause/play (and first tap unlocks audio). Tap targets ≥44px. Respect title-safe insets.
- **Radius:** cards `1px` (sharp/editorial), controls `16px`, pills `9999px`. Spacing on the 8px base.

---

## 5. Surfaces to design (all of them)

Design each surface, all reachable in the prototype. For each: the purpose, the must-have elements, the key
states, and the interactions.

### 5.1 — App shell / chrome
- Full-bleed dark canvas, real safe-area insets, status-bar-aware.
- **No persistent bottom tab bar** competing with the reel gesture model. Navigation is gesture-first; a
  single discreet entry (e.g. top-corner) reaches profile/following/settings.
- A subtle, persistent **"finite progress" indicator** is part of the chrome on the reel (see 5.2).

### 5.2 — Auto-play vertical reel (home, the highest-risk surface)
- **Poster image as full-screen background** (mocked) with the **fixed overlay frame** on top: brand mark
  (top), headline + segment chip + timestamp over the bottom scrim, and an **action row**
  (save · share · follow-story · open-detail · voice). Lay out the action row in a way that doesn't fight
  the swipe gestures — a right rail or a low row, your call (flag it).
- **Sound-off captions** in the lower-middle third with word-by-word reveal + one yellow keyword/sentence.
- **Anti-doom-scroll finiteness, made visible:** a finite progress treatment — e.g. `07 / 30` in JetBrains
  Mono and/or a segmented top bar showing how many stories remain. This is the soul of the product; make it
  feel like a *briefing with an end*, not a feed.
- **Per-story playback progress** (thin bar) + **auto-advance** to the next story when audio ends; tap to
  pause/play. First tap unlocks audio (mirror the iOS gesture-unlock reality — show a tasteful
  "tap to start" first-run affordance).
- A whisper-quiet hint of the right→detail / left→voice gestures (peek edges or a one-time coach mark).
- Two-voice cue is welcome but minimal (e.g. a tiny anchor-duo indicator), never gimmicky.

### 5.3 — Story Detail View (swipe right — the moat surface)
- An **editorial reading surface**: Inter headline/dek, **Playfair Display** chunked body sized for a
  <100-second read, modular panels, sharp `1px` card edges, masked/staggered entrance reveal.
- **Supporting visuals:** room for a chart / timeline / image block.
- **Trust layer (design this with care — it's the differentiator), all stats in JetBrains Mono:**
  - **Coverage breakdown bar (L / C / R)** using bias colors — left `#3B82F6`, center `#A1A1AA`, right
    `#E8B7BC` — with counts.
  - **Outlet count** ("covered by 24 outlets").
  - **Story timeline** (how it developed).
  - **Blindspot flag** badge ("Blindspot — under-covered on the right/left").
  - **"Read the opposing view"** entry.
- Back to reel via swipe-left/back. (See §9 on how much real estate trust deserves.)

### 5.4 — Typed Q&A (lives at the bottom of Detail — the "interrogate-in-place" core)
- A pinned input at the bottom of the Detail view: "Ask this story…" with 2–3 **suggested questions**
  ("What led to this?", "Who's affected?").
- **Answers cite the source** — show inline source-citation chips on each answer.
- **A grounded-refusal state** is mandatory: when the source can't support an answer, the UI says so
  cleanly ("I can only answer from this story's source — that isn't covered here"). News has zero tolerance
  for invented facts; the design must make grounded vs. can't-answer legible.
- Thinking/loading state for an answer.

### 5.5 — Voice mode (swipe left — Gemini Live, hands-free)
- Calm, dark, **eyes-off-the-road minimal**. A central listening orb / waveform that reacts to voice, with
  clear **listening vs. responding** states (it only responds when addressed).
- Optional live transcript (user + assistant), kept secondary.
- **First-run mic-permission** prompt. Exit back to reel.
- Same grounded/refusal discipline as typed Q&A.

### 5.6 — Onboarding / interests
- First-run value prop that sells the **finite "caught up" promise** and teaches the tap-to-start /
  audio-on gesture.
- **Pick interest categories** (tie to the segment set + accents).
- Minimal sign-in (treat as Supabase auth — email/Apple; mock it).
- See §9: surface the "top world stories always included + your picks" composition idea visually if you can.

### 5.7 — Follow + "what's new since you last watched"
- **Follow a story** (from the reel action row and/or Detail).
- A **Following / "what's new"** view: followed stories that have new developments since last watch, with a
  clear "new since you last watched" marker.

### 5.8 — Shared states (don't skip these)
- **Loading:** feed skeleton (placeholder posters), digest buffering.
- **Empty / the payoff:** **"You're all caught up — 30 / 30. Come back tomorrow."** This finish-line screen
  is a signature, on-brand moment — the anti-doom-scroll reward. Design it to feel *earned*, not like an
  error. Make it memorable.
- **Error:** failed to load, offline, mic denied, Q&A failure — all calm and on-brand.

---

## 6. Interaction & motion model

- **Snap-scroll reel:** vertical scroll-snap between full-screen stories; momentum feels native.
- **Lateral gestures:** swipe right pushes in the Detail (it should feel like the story *opens*, not a new
  tab); swipe left brings up Voice. Provide visible transitions, not hard cuts.
- **Auto-advance:** when a story's audio "ends" (mock a ~10–15s timer in the prototype), advance to the next
  with a smooth transition; pause halts it.
- **Caption reveal:** word-by-word, synced to the mock timer; highlight one keyword per sentence in
  `#FACC15`.
- **Motion philosophy (from design-language):** restrained, smooth — masked reveals, staggered entrance,
  press/hover lift. Motion is *secondary to content*. Never motion *under* live text (poster §10).
- Honor `prefers-reduced-motion`.

---

## 7. Mock data (hardcode this shape; ship ~5 stories across segments)

Give yourself ~5 sample stories so segment accent-coding and the trust layer are visible. Suggested shape:

```js
const story = {
  id: "s1",
  segment: "geopolitics",          // geopolitics | markets | tech | sport | wildcard
  accent: "#EF4444",               // from the segment table (one per poster)
  headline: "…",                   // overlay only — never baked into the image
  dek: "…",
  archetype: "versus",             // poster §3: versus|single-icon|metaphor|rise-fall|hero|surreal
  captions: [                      // word-by-word; mark the highlight keyword
    { text: "Two nations", highlight: null },
    { text: "edge", highlight: "edge" }, /* … */
  ],
  detail_chunks: ["…", "…"],       // Playfair body, <100s read total
  trust: {
    coverage: { left: 9, center: 7, right: 3 },   // L/C/R outlet counts
    outlet_count: 19,
    blindspot: "right",                            // or null
    timeline: [{ when: "08:10", what: "…" }],
    opposing_view: "…",
  },
  suggested_questions: ["What led to this?", "Who's affected?"],
};
```

Pick stories that exercise different accents (an ember-red geopolitics story, a green markets story, a cyan
tech story, an amber sport story, a blush wildcard) so the system's coherence-through-variation shows.

---

## 8. Constraints & "do not" list

**Build-faithful (so it ports):**
- iPhone-only, 9:16, dark, safe-area-respecting, touch-first. Map tokens into `tailwind.config` so class
  names survive the port to Tailwind 4.
- Assume `output: "export"` static SPA reality (stack-notes): no server-rendered magic; everything is
  client-side mock state.

**Do NOT:**
- Invent a new palette, new fonts, or a parallel token system. Use design-language.md.
- Use more than one accent per poster, or let an accent collide with `#3B82F6` / `#FACC15`.
- Bake headline/caption text into a poster image — overlay is always a separate layer.
- Add a TikTok-style infinite-feel or any gimmick that reads as *entertainment* (it gets misclassified and
  it betrays the product). Equally, don't make it stiff or corporate.
- Add a persistent bottom tab bar that fights the gesture model.
- Build a real backend, call real APIs, or generate real images.
- Sacrifice caption/text legibility over posters — always scrim; aim for WCAG-ish contrast in the text band
  (poster §7).

---

## 9. Design judgment calls to make — and flag in the decisions file

These are genuinely open in the product docs. Make a confident visual call and record it:
1. **Trust-layer real estate (brief Open Q4):** it's the moat signal but the persona is a casual commuter.
   How prominent in Detail — hero band, or a tidy expandable strip? Decide and show it.
2. **"World" vs. "my field" feed composition (brief Open Q3):** general FOMO wants breadth; personalization
   narrows. Recommended visual model: a guaranteed "top world stories" tier + a personalized tier. Express
   it in onboarding and/or a subtle reel marker.
3. **Passive vs. active emphasis (brief Open Q2):** the habit is driven by the passive reel; the
   differentiator is active interrogation. How loudly should the reel advertise "you can ask this story
   something"? Find the balance in the action row / coach marks.
4. **Action-row placement:** right rail vs. low row, given the lateral swipe gestures. Pick the one that
   least competes with swipe-right/left.

---

## 10. Definition of done (checklist)

- [ ] `index.html` runs with no build step (open directly or `python3 -m http.server`) and shows an iPhone
      frame at ~393×852 with correct safe-area insets.
- [ ] All §5 surfaces designed and **reachable**: reel, detail + trust, typed Q&A (incl. grounded-refusal),
      voice (listening/responding), onboarding, follow + "what's new", and the loading / "all caught up" /
      error states.
- [ ] Gestures real: swipe up/down (reel), swipe right (detail), swipe left (voice), tap (pause/play +
      audio unlock), with smooth transitions and `prefers-reduced-motion` respected.
- [ ] Auto-advance + per-story progress + the **finite "07 / 30" finiteness** treatment present.
- [ ] Captions reveal word-by-word with exactly one `#FACC15` keyword per sentence, legible over the poster.
- [ ] Posters render as the **fixed overlay frame** over mocked duotone backgrounds; one accent each;
      segment-coded; no text baked in.
- [ ] Tokens, type (Inter / Playfair / JetBrains Mono), spacing, radius all match design-language.md, mapped
      into `tailwind.config`.
- [ ] Trust layer complete: L/C/R coverage bar, outlet count, timeline, blindspot, opposing-view — all in
      JetBrains Mono.
- [ ] `reference/ui-design-decisions.md` written, with the §9 calls resolved and any provisional-accent
      contrast flags raised.
- [ ] Reads as a serious, finite, completable briefing — calm, credible, never entertainment, never stiff.

---

## 11. Keep checking against the feel

Before you call any surface done, ask: *Does this feel like a finite briefing I'll finish on my commute,
that I trust because it shows its work, and that I can stop and question at any moment — without ever
feeling like I'm doom-scrolling?* If not, it's not done.
