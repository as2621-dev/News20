# blip — Design Guide for Claude Code

Single-file handoff. Everything an agent or engineer needs to build blip surfaces that match the design system. Source of truth: the "Blip" design-system project (`tokens/`, `components/`, `ui_kits/`).

---

## 1. Brand in one paragraph

**blip** is an audio-first news app. A *blip* is a radar/audio signal — the whole identity hangs off that metaphor: the wordmark's tittle is a glowing signal dot with three sound waves; voice mode flips those waves between listening and responding; every "live" element is a small glowing dot. Product promise: a **finite, calm, trustworthy daily briefing** (~30 stories, "26/30" counters, an "all caught up" finish line) — the anti-doom-scroll. "blip" is **always lowercase**, even at sentence start.

## 2. Color tokens

```css
:root {
  /* canvas — dark only, no light mode */
  --bg: #020617;            /* near-black navy, everything sits on this */
  --bg-2: #070b18;          /* lifted panel navy (website) */
  --sheet-top: #0b1120;     /* popup-sheet gradient start */
  --sheet-bottom: #070b14;  /* popup-sheet gradient end */

  /* ink */
  --ink: #ffffff;
  --muted: #9aa3b2;         /* secondary copy (website) */
  --muted-2: #5f6878;       /* tertiary / micro labels */
  --text-secondary: #A1A1AA;/* app secondary text + LISTENING gray */

  /* hairlines & surfaces — always translucent white on dark */
  --line: rgba(255,255,255,0.10);
  --line-soft: rgba(255,255,255,0.06);
  --surface-card: rgba(255,255,255,0.025);
  --surface-raised: rgba(255,255,255,0.06);
  --surface-pressed: rgba(255,255,255,0.10);

  /* the three reserved colors */
  --hi: #FACC15;            /* THE yellow — see rules below */
  --blue: #3B82F6;          /* Ask / primary actions ONLY */
  --light: #D1D4BD;         /* the ONE light card (opposing view) */
  --light-ink: #1b1d18;
  --blush: #E8B7BC;         /* refusal / blindspot / bias-right */

  /* per-story segment accents — exactly one active at a time via --accent */
  --seg-geopolitics: #EF4444;
  --seg-markets: #22C55E;
  --seg-tech: #22D3EE;
  --seg-sport: #F59E0B;
  --seg-wildcard: #E8B7BC;
  --accent: var(--seg-geopolitics); /* set per story on a container */

  /* trust / bias scale */
  --bias-left: #3B82F6;
  --bias-center: #A1A1AA;
  --bias-right: #E8B7BC;

  /* karaoke caption states */
  --caption-dim: rgba(255,255,255,0.30);
  --caption-spoken: rgba(255,255,255,0.50);
  --caption-active: #ffffff;
  --state-listening: #A1A1AA;   /* voice: gray */
  --state-responding: #FACC15;  /* voice: brand yellow */
}
```

**Color discipline (the core rule):** monochrome white/gray UI + exactly one accent at a time.
- `#FACC15` yellow is THE pop: **one** highlighted keyword per caption sentence, the wordmark tittle, the RESPONDING voice state, "done" states. Never decorative.
- `#3B82F6` blue only for Ask/primary actions and bias-left. Never fill a button with it.
- The segment accent cascades to: ambient wash, seg-chip dot, current finite segment, progress accents, stat card.
- `#E8B7BC` blush: never small/thin text on dark (contrast dips).

## 3. Type

- **Inter** — chrome, headlines, UI. 800 wordmark, 700 headlines (ls -0.02em), 400–500 UI.
- **Playfair Display** — the editorial voice. 700 hero captions (23px, lh 1.25, ls -0.01em), 400 reading body (17.5px, lh 1.72, color #E8E8E4).
- **JetBrains Mono** — every stat/label/state. Uppercase, letterspaced (0.12em labels, 0.13em micro, 0.24em voice states), `·` middot separators. Sizes 9–12px.

Google Fonts import:
```css
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&family=JetBrains+Mono:wght@400;500;600;700&display=swap");
```

Rule of thumb: **serif = the news · mono = the machine · sans = the chrome.**

## 4. Shape, space, elevation

```css
--radius-card: 1px;      /* editorial/trust cards — near-sharp, signature move */
--radius-panel: 4px;     /* stat blocks, opposing-view panels */
--radius-control: 16px;  /* buttons, fields */
--radius-field: 15px;    /* composer bars */
--radius-sheet: 26px;    /* bottom-sheet top corners */
--radius-pill: 9999px;   /* chips, dots, icon rails */
--hit-target: 44px;      /* min tap size */
/* spacing: 8px base (4/8/12/16/20/24) */
```

- **Borders over shadows**: flat translucent-white surfaces + hairline borders. Shadows only for sheet/device elevation (deep soft black: `0 -24px 60px -20px rgba(0,0,0,.8)`).
- **Live = a glowing dot**: 5–8px pill, `box-shadow: 0 0 8–12px currentColor`.
- Backdrop blur only on floating chrome (scrolled nav, side-rail buttons, ask field, sheet scrims).

## 5. Motion

```css
--ease-out: cubic-bezier(0.22, 0.61, 0.36, 1); /* THE curve for everything structural */
--ease-flip: cubic-bezier(0.65, 0, 0.2, 1);    /* voice-mark mirror flip */
/* durations: press 140ms · hover 160ms · sheet 380ms · lateral layer 420ms · reveal 520ms */
```

- Press = `scale(0.97)` (icon buttons 0.9). Hover = bg steps `rgba(255,255,255,.05 → .10)` or color lightens; never underline.
- Ambient loops are very slow (15–30s drift/breathe). **Nothing bounces.**
- `prefers-reduced-motion: reduce` must disable all ambient/karaoke/signal animation.

## 6. Backgrounds: the ambient wash

No photography as-is. A poster becomes a blurred, slowly drifting duotone wash tinted by `--accent`, plus scrims so text always clears contrast:

```css
.ambient::before {
  content: ""; position: absolute; inset: -20%;
  background:
    radial-gradient(58% 42% at 50% 20%, color-mix(in oklab, var(--accent) 70%, transparent) 0%, transparent 68%),
    radial-gradient(85% 60% at 72% 6%, color-mix(in oklab, var(--accent) 36%, transparent) 0%, transparent 62%),
    var(--bg);
  filter: blur(44px) saturate(0.9); opacity: 0.95;
  animation: drift 26s ease-in-out infinite alternate;
}
/* bottom scrim (66% height): linear-gradient(to top, var(--bg) 10%, color-mix(in oklab, var(--bg) 82%, transparent) 46%, transparent 100%) */
/* top scrim (32%): linear-gradient(to bottom, color-mix(in oklab, var(--bg) 78%, transparent), transparent) */
```

## 7. The wordmark (code, not an image)

There is **no raster logo**. Render "blip" in Inter 800 lowercase, ls -0.05em; replace the i's tittle with a dot + three arcs. Yellow signal on the website, white (`mono`) inside app chrome. Everything is em-scaled off font-size.

```html
<span class="blip" style="font-size:24px">bl<span class="bi">ı<i class="tittle"><b class="bdot"></b><svg class="blip-sig" viewBox="0 0 11 14" fill="none"><path class="bw bw1" d="M2.22 4.70 A3.2 3.2 0 0 1 2.22 9.30"/><path class="bw bw2" d="M4.31 2.54 A6.2 6.2 0 0 1 4.31 11.46"/><path class="bw bw3" d="M6.39 0.39 A9.2 9.2 0 0 1 6.39 13.61"/></svg></i></span>p</span>
```

```css
.blip { font-family: Inter; font-weight: 800; letter-spacing: -0.05em; color: #fff; line-height: 1; position: relative; white-space: nowrap; }
.blip .bi { position: relative; display: inline-block; }
.blip .tittle { position: absolute; left: 50%; bottom: 0.72em; transform: translateX(-0.09em);
  display: flex; align-items: center; gap: 0.05em; height: 0.46em; pointer-events: none; }
.blip .bdot { width: 0.18em; height: 0.18em; border-radius: 9999px; background: var(--hi); box-shadow: 0 0 0.16em rgba(250,204,21,.9); }
.blip .blip-sig { height: 0.46em; width: auto; overflow: visible; }
.blip .bw { fill: none; stroke: var(--hi); stroke-width: 2.2; stroke-linecap: round; }
.blip .bw1 { opacity: 1; } .blip .bw2 { opacity: .88; } .blip .bw3 { opacity: .74; }
/* .blip.mono → dot/waves white. .blip.glow → waves pulse outward 2.6s. */
```

## 8. The voice mark (voice mode)

The dot is blip. **LISTENING**: gray `#A1A1AA`, waves mirrored to open LEFT (your voice arriving at the dot), ripple cascades outer→inner. **RESPONDING**: brand yellow `#FACC15`, waves open RIGHT (blip speaking), ripple inner→outer. Mirror flip transitions with `--ease-flip` over 550ms; the whole mark breathes 4.2s.

```html
<div class="vmark listening">
  <svg class="vm-sig" viewBox="0 0 30 44" fill="none">
    <path class="vm-wave w3" d="M23.68 4.74 A24 24 0 0 1 23.68 39.26"/>
    <path class="vm-wave w2" d="M18.82 9.78 A17 17 0 0 1 18.82 34.22"/>
    <path class="vm-wave w1" d="M13.95 14.81 A10 10 0 0 1 13.95 29.19"/>
    <circle class="vm-dot" cx="7" cy="22" r="3.1"/>
  </svg>
</div>
<div class="vs-state live">LISTENING</div> <!-- .resp → RESPONDING, yellow -->
```

```css
.vmark { width: 120px; height: 120px; display: grid; place-items: center; animation: vmBreathe 4.2s ease-in-out infinite; }
.vm-sig { height: 82px; overflow: visible; transition: transform .55s cubic-bezier(.65,0,.2,1); }
.vmark.listening .vm-sig { transform: scaleX(-1); }
.vm-wave { fill: none; stroke: #A1A1AA; stroke-width: 2.4; stroke-linecap: round;
  filter: drop-shadow(0 0 6px rgba(161,163,170,.45)); animation: vmPing 2.1s ease-in-out infinite; }
.vm-wave.w1 { --o:.95 } .vm-wave.w2 { --o:.68 } .vm-wave.w3 { --o:.46 }
.vm-dot { fill: #fff; transform-box: fill-box; transform-origin: center;
  filter: drop-shadow(0 0 6px rgba(255,255,255,.85)); animation: vmDot 2.1s ease-in-out infinite; }
.vmark.responding .vm-wave { stroke: #FACC15; filter: drop-shadow(0 0 9px rgba(250,204,21,.6)); }
.vmark.responding .w1 { animation-delay: 0s } .vmark.responding .w2 { animation-delay: .16s } .vmark.responding .w3 { animation-delay: .32s }
.vmark.listening  .w3 { animation-delay: 0s } .vmark.listening  .w2 { animation-delay: .16s } .vmark.listening  .w1 { animation-delay: .32s }
@keyframes vmPing { 0%,68%,100% { opacity: var(--o,.6) } 34% { opacity: 1 } }
@keyframes vmDot { 0%,100% { transform: scale(1); opacity: .9 } 40% { transform: scale(1.28); opacity: 1 } }
@keyframes vmBreathe { 0%,100% { transform: scale(1) } 50% { transform: scale(1.04) } }
.vs-state { font-family: "JetBrains Mono"; font-size: 11px; font-weight: 600; letter-spacing: .24em; }
.vs-state.live { color: #A1A1AA } .vs-state.resp { color: #FACC15 }
```

## 9. Karaoke caption

Playfair 700 23px, dim words light to white as spoken, exactly ONE yellow keyword per sentence:

```css
.caption { font-family: "Playfair Display", serif; font-weight: 700; font-size: 23px; line-height: 1.25;
  letter-spacing: -0.01em; text-wrap: balance;
  text-shadow: 0 1px 18px rgba(2,6,23,.85), 0 1px 3px rgba(2,6,23,.9); }
.caption .w { color: rgba(255,255,255,.30); transition: color 220ms ease; }
.caption .w.spoken { color: rgba(255,255,255,.50); }
.caption .w.active { color: #fff; }
.caption .w.hl { color: #FACC15; }
```

## 10. Key component recipes

- **Primary button**: white bg, `#06080f` ink, 600 weight, radius 16px (app) / pill (website). Ghost: `rgba(255,255,255,.05)` + `--line` border. Never blue-filled.
- **Icon buttons**: 44px square r16 (action rows) or 40px pill + backdrop-blur (side rail). Active = yellow; follow-active = `--accent`.
- **Chips**: mono 11px pill, `--line` border, `rgba(255,255,255,.03)` bg; selected inverts to solid white with dark ink. Optional glowing dot.
- **Composer field**: 52–56px, r15–17, `rgba(255,255,255,.06)` bg, `rgba(255,255,255,.22)` border; white send square (38px, r11) dims to `rgba(255,255,255,.08)` when empty.
- **Finite bar**: one 2.5px segment per story; done = `rgba(255,255,255,.7)`, current = `--accent`, rest `.18`. Pair with mono `26/30` counter.
- **Trust card**: r1px, `rgba(209,212,189,.16)` border, `rgba(255,255,255,.025)` bg; L/C/R bias bar (blue/gray/blush widths ∝ counts); blindspot badge in blush `⌀ BLINDSPOT: RIGHT`.
- **Refusal bubble**: `rgba(232,183,188,.08)` bg, `rgba(232,183,188,.3)` border, r18; mono label `⌀ CAN'T ANSWER FROM SOURCE`; canonical copy: *"I can only answer from this story's source — that isn't covered here."* Never fake an answer.
- **Opposing-view card**: THE one light surface — `#D1D4BD` bg, `#1b1d18` ink, r1px, Playfair quote.
- **Bottom sheet**: r26 top corners, gradient `#0b1120 → #070b14`, hairline top border, 38×4px grab handle, rises 380ms with `--ease-out` while the reel behind dims to brightness(.42) and scales .97.
- **Sig (mic) button**: 58px white circle, mic icon, two expanding pulse rings (2.6s, staggered 1.3s).

## 11. Copy rules

- Calm, confident, second person: "Tap to start your briefing", "Ask anything about this story…", "Perfect — that's your briefing tuned". Never hype.
- Two registers: *editorial* (Playfair, complete sentences, em-dashes) vs *system* (mono, ALL-CAPS, letterspaced, `·` separators: "COVERED BY 19 OUTLETS", "PRESS TO TALK · OR TYPE YOUR OWN").
- **No emoji, ever.** Unicode `·`, `⌀`, `›` serve as micro-glyphs.
- Honesty is a feature: refuse rather than invent (see Refusal).

## 12. Iconography

Inline SVG line icons, 24×24 viewBox, `stroke="currentColor"`, stroke-width 1.6–2.2, round caps/joins, shipped as a `<defs>` sprite + `<use href="#i-name">`. Inventory: save, share, follow/following, ask, voice, search, profile, back, close, play, pause, send, blindspot, clock, chevron-d, spark, keyboard, doc, arrow, bell. No icon fonts.

## 13. Layout constants (app)

iOS frame 393×852; simulated safe areas: 59px top (Dynamic Island), 34px bottom (home indicator). Screen radius 54px inside the bezel. All hit targets ≥44px. Lateral navigation: detail slides from right, voice from left (420ms `--ease-out`); article rises from bottom; the reel behind any open layer dims + scales back as a depth cue.

## 14. Never do

- Light mode, colored button fills, more than one yellow keyword per sentence
- Photography without the duotone-wash treatment
- Emoji, icon fonts, bouncy easing, infinite decorative loops on content
- Rounded-corner-heavy cards (cards are 1px!), drop shadows on flat cards
- "Blip"/"BLIP" capitalization, plain-text wordmark
