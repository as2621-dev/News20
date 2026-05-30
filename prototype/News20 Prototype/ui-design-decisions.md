# blip — UI Design Decisions

Companion to the prototype (`index.html` + `app.js` + `data.js` + `styles.css`).
Logs the calls made during the design pass, the §9 open judgment calls and how each
was resolved, and notes for the engineering team porting to Next.js 15 + Tailwind 4.

> **Brand:** the product is now **blip** (was “News20”). A *blip* is a radar/audio signal
> — exactly right for an audio-first news app. **Logo:** the “blip” wordmark where the
> **tittle of the “i” is a glowing signal blip** (Inter 800, lowercase, monochrome white so
> it never collides with the per-segment accents or reserved `#3B82F6`/`#FACC15`). A
> standalone dot+ring mark (`.blip-mark`) exists for square/icon spots. Used in the reel
> chrome, onboarding, and loading splash.

> Built per `reference/ui-design-brief.md`. Tokens, poster rules and product reasoning
> taken verbatim from `design-language.md`, `poster-pipeline.md`, `product-brief.md`,
> `master-plan.md`, `stack-notes.md`. Where the brief and docs conflicted, docs won on
> tokens; the brief won on deliverable shape.

---

## 0. The one direction that overrides the brief: **audio-first**

The brief frames the reel as a **poster image full-screen background** with small
lower-third captions. The product owner re-directed this pass to be **audio-first**:
the audio digest is the focus, *not* video/imagery. So:

- **Captions are the hero.** Large two-to-three-line **Playfair Display** captions sit in
  the lower-center band (~40% of height), karaoke-style: the sentence is dim, the current
  word lights to white, and exactly **one keyword per sentence pops `#FACC15` yellow**.
- **The poster recedes to ambient.** The graded poster (`poster-pipeline.md` §6) becomes a
  **blurred, dimmed, slowly-drifting duotone wash** behind the captions — segment-accent
  coloured, with a dissolved silhouette hint in the upper third (honours the §2 "silhouette
  in the upper third, dark lower 40%" mock while reading as atmosphere, not an image).
- **Audio presence is ultra-minimal:** a thin per-story progress bar + a tiny speaker label
  (`● ALEX` / `● JORDAN`, alternating per sentence) + a `PLAYING/PAUSED` mono state. No
  waveform clutter on the reel (per owner direction).

**Why this still serves the product:** an audio-first, big-caption, calm dark surface reads
even *less* like TikTok entertainment than the original — it's a "now playing" briefing, not
a video feed. The anti-doom-scroll soul is intact.

### The brand frame, re-located
The brief calls the fixed overlay frame the #1 brand lever. With the poster gone to ambient,
**the frame becomes the fixed chrome lockup**: wordmark (top-left) · finite `26/30` counter +
segmented progress bar (top) · persistent segment-coded headline zone · bottom scrim · low
action row. This lockup is identical on every story; only the wash + words beneath change.

---

## 1. §9-Q4 — Trust-layer real estate → **compact, expandable strip (not a hero band)**

The persona is a casual commuter, so trust is expressed as a **confident compact card** high
in the Detail view: coverage L/C/R bias bar (bias colours from `design-language.md`:
left `#3B82F6`, center `#A1A1AA`, right `#E8B7BC`), `COVERED BY N OUTLETS`, and a blindspot
badge — all in **JetBrains Mono**. The **story timeline** lives in an expandable drawer
(opt-in, doesn't tax a casual reader). The **opposing view** is pulled out as the one
**light `#D1D4BD` surface card** lower in the read (the sparing use the tokens prescribe) —
it earns prominence as the editorial "we'll show you the other side" moment.
*Result: trust is visible and credible at a glance, deep on demand.*

## 2. §9-Q3 / Q2 — "World" vs "my field" → **guaranteed world tier + personalized tier**

Expressed in the **voice-agent onboarding** (step 2): blip's opening line frames it (“skip
the checkboxes”) and the profile panel header reads *“YOUR PROFILE · TOP WORLD STORIES
ALWAYS IN”* — personalization only reorders, the world tier is guaranteed. The finite
`N/30` counter reinforces that the whole world is covered. (A subtle per-story “world tier”
marker on the reel is a recommended follow-up; not built this pass to keep the reel calm.)

### Onboarding interests → **a voice agent, not a checkbox grid** (owner direction)
Instead of tapping interest chips, **blip interviews the user**: a brand-white listening orb
+ waveform asks ~3 questions (what you follow → world-first vs niche → facts vs the “why”)
and **builds a live interest profile** — tags light up as signals are detected. **Voice is
primary** (tap-to-answer mic); a **typed chat is the optional fallback** (“type instead”,
with keyword→segment extraction). Ends with *“Perfect — that's your briefing tuned”* + a
`Build my briefing` CTA. In the prototype the conversation is scripted/deterministic; **port
note:** wire to the same Gemini Live agent as Voice mode, extracting an interest profile from
the transcript (function-calling) rather than the canned `VP_TURNS`.

### Sign-in → **email-only, passwordless** (owner direction)
The “Continue with Apple” button is removed (avoids the Sign-in-with-Apple review/entitlement
overhead). Step 3 is a single **email** field → *“we'll email you a sign-in link — no
password”* (magic-link framing), with inline validation. Maps cleanly to Supabase email OTP /
magic-link auth. Re-add Apple later only if App Store review requires it for the email option.

## 3. §9-Q2 (passive vs active) — **reel whispers, detail shouts**

The reel advertises interrogation quietly: a primary-blue **`Ask`** button in the action row
+ a one-time edge coach-peek (`READ ›` right, `VOICE ›` left). The interrogation surfaces
themselves (Detail Q&A, Voice) are where the differentiator is loud. This matches the brief's
"find the balance" — the passive loop drives the habit, the active layer is one tap away
without competing with playback.

## 4. §9 — Action-row placement → **low row, not right rail**

Lateral swipes own the screen edges (right = Detail, left = Voice), so a right rail would
fight them. The action row is a **low horizontal row** above the home indicator
(`save · share · follow · ask · voice`), all ≥44px hit targets. It sits below the caption
band and above the bottom safe-area inset.

---

## 5. Type & token fidelity

- **Inter** — chrome, headlines, deks, UI labels. **Playfair Display** — the reel hero
  captions *and* the Detail reading body (the prestige move; the owner chose "lean
  prestige/editorial"). **JetBrains Mono** — every trust stat, counter, timestamp, metadata.
- **Caption note / deviation flag:** `design-language.md` implies display-type (Inter) for
  captions; we use **Playfair Display bold** for the hero captions. It satisfies the doc's
  "bold white, black outline, word-by-word" rule and is the single biggest lever making
  News20 not look like a video app. Flagging for sign-off.
- Radius: cards `1px`, controls `16px`, pills `9999px`. Spacing on the 8px base. All mapped
  into the inline `tailwind.config` (`bg-background`, `text-caption-highlight`, `font-serif`,
  `rounded-card`, `seg-*`, `bias-*`) so class names port to the Tailwind 4 build.

## 6. Provisional segment accents — contrast on `#020617` (poster-pipeline §8 open item)

All checked against near-black for large/bold use:

| Segment | Hex | Verdict on `#020617` |
|---|---|---|
| Geopolitics | `#EF4444` | OK |
| Markets | `#22C55E` | OK |
| Tech | `#22D3EE` | OK (very high contrast) |
| Sport | `#F59E0B` | OK |
| Wildcard / bias-right | `#E8B7BC` | **Flag:** blush is light & low-chroma. Fine for large text, the bias-right bar segment, and badges; **do not** use for small/thin mono text on dark — it dips toward the 4.5:1 line. Used only at large sizes / as fills here. |

No accent collides with UI-reserved `#3B82F6` (actions) or `#FACC15` (caption highlight).
One accent per story, set on `--accent` and cascaded to the wash, chip, speaker dot,
progress, and key-figure card.

## 7. Grounded Q&A + the mandatory refusal (Decision #5)

- Suggested questions return canned **grounded** answers with **source-citation chips**.
- Typed free-text is matched against a **curated per-story topic list** (`data.topics`).
  On-topic → grounded answer (closest canned answer). **Off-topic → the mandatory
  grounded-refusal** state (`"I can only answer from this story's source — that isn't
  covered here"`), visually distinct (blush border, `⌀ CAN'T ANSWER FROM SOURCE`).
- A thinking/typing state precedes every answer.
- **Port note:** swap `resolveAnswer()` for the real RAG retriever + verification stage; keep
  the on-topic/refusal *visual contract* exactly — it's how users learn to trust the system.

## 8. iOS / Capacitor realities honoured (stack-notes)

- **Safe areas simulated:** ~59px top (Dynamic Island), ~34px bottom home indicator; all
  content respects them.
- **First-tap-to-unlock-audio:** a tasteful "Tap to start your briefing" first-run overlay
  mirrors the muted-autoplay → gesture-unlock reality; playback (caption karaoke + progress +
  auto-advance) only begins after it.
- **Mic permission** is a real gate in Voice mode (Allow → conversation; Not now → calm
  mic-denied state). Maps to `NSMicrophoneUsageDescription` + Capacitor permission request.
- **Auto-advance** mocks the ~13s digest with a timer; on "audio end" it advances with a
  smooth transition; tap pauses. `prefers-reduced-motion` is honoured throughout
  (ambient drift, orb, reveals, lateral transitions all disabled).

## 9. Reachable surfaces (all of §5)

Onboarding (3 steps) → Loading skeleton → **Reel** → swipe-right **Detail + trust + Q&A**
(incl. refusal) → swipe-left **Voice** (permission / listening / responding / mic-denied) →
**Profile sheet** (discreet top-right entry; doubles as a state navigator for reviewers) →
**Following / "what's new since you last watched"** → **"All caught up — 30/30"** finish line →
**Error** (offline). Gestures: swipe up/down (next/prev), right (detail), left (voice), tap
(pause/play + first-tap audio unlock).

## 10. Known prototype shortcuts (for the port)

- 5 hardcoded stories occupy feed positions 26–30 so the finite "all caught up" finish line
  is reachable in-demo by swiping. Real feed = ~30 ranked stories starting near `01/30`.
- Posters are CSS duotone washes, not generated images (the brief's mock guidance). The real
  pipeline (`poster-pipeline.md`) feeds graded images; in audio-first mode they'd drive the
  ambient wash's colour/silhouette.
- Voice "conversation" is a scripted listen→respond loop; wire to Gemini Live.
- Lateral open/close uses threshold gestures + CSS transitions (not finger-following drag);
  a framer-motion drag-to-follow is the recommended upgrade in the React build.

---

*Prototype runs with no build step — open `index.html` directly or `python3 -m http.server`.
Tailwind via Play CDN with an inline config; fonts via Google Fonts.*
