# Design Language

**Why this doc exists:** `/run-phase` builds UI from *this file*, not the remote design library — so the tokens below are inlined and self-contained. It defines the visual language for News20's reel + detail views.

**When to update:** If the base system changes, or when a real token is refined during build.

## Chosen base system

**Spatial Editorial System** — `spatial-editorial-system`
URL: https://www.aura.build/design-systems/spatial-editorial-system
Meta: https://raw.githubusercontent.com/ashesh2621/design-references/main/design-systems/spatial-editorial-system.md
HTML preview: https://raw.githubusercontent.com/ashesh2621/design-references/main/design-systems-html-previews/spatial-editorial-system.html

**Why it fits:** A near-black canvas (`#020617`) is exactly right for a TikTok-style full-screen video reel — video frames and yellow-highlight captions pop against black, and the dark base reads as "serious news," not entertainment. Inter for display + **Playfair Display** for body gives the Story Detail View editorial reading credibility (it's a *reading* surface, sub-100s). **JetBrains Mono** labels are perfect for the bias L/C/R stats, outlet counts, and timestamps — the "we did the research" trust signals. Rejected alternative: *Aura Mobile Flow* (light background + lime) — friendlier mobile patterns but a light canvas is wrong for a video-first dark reel.

## Tokens (inlined — source of truth)

### Colors
```
primary        #3B82F6   (actions, active states, "follow")
secondary      #D1D4BD   (muted surfaces / sage)
accent         #E8B7BC   (soft highlight / blush)
background      #020617   (near-black canvas — reel + app base)
surface         #D1D4BD  (cards in detail view; use sparingly on dark)
text-primary   #FFFFFF
text-secondary #A1A1AA
border         #D1D4BD
caption-highlight #FACC15 (yellow — one keyword/sentence in captions; project addition)
bias-left      #3B82F6   bias-center #A1A1AA   bias-right #E8B7BC  (project addition)
```
Note: on the dark reel, prefer text/overlays directly on `background`; reserve `surface` (#D1D4BD) for detail-view cards where a light panel aids reading.

### Typography
```
display-lg   Inter, 64px, weight 500, line-height 1.04   (hooks, headlines)
body-md      Playfair Display, 16px, weight 400, line-height 1.6   (readable story text)
label-md     JetBrains Mono, 12px, weight 600, line-height 1.2   (stats, metadata, bias labels)
```
Captions (sound-off, project-specific): bold, white, black outline, lower-middle third, word-by-word reveal, one `caption-highlight` keyword per sentence.

### Spacing & shape
```
spacing base 8px · gap 16px · card-padding 24px · section-padding 80px
radius: card 1px (sharp/editorial) · control 16px · pill 9999px
```

## Sections to adopt
- **Editorial density + modular panels** for the Story Detail View (headline, dek, key-figures, bias bar, source list).
- **Restrained, smooth motion** — masked reveals, staggered entrance, hover/press lift. Keep secondary to content.
- **Mono metadata treatment** for all trust stats (coverage breakdown, outlet count, timeline, blindspot badge).

## Voice / tone
Serious but not stiff; informative, credible, calm. The anti-doom-scroll: the UI should feel like a finite, completable briefing — not an infinite entertainment feed. No playful gimmicks that would let the platform misclassify it as entertainment.

## Skills to rely on (remote design library, fetch when building)
Fetch the index when building motion-heavy UI and pick by name:
```bash
curl -s https://raw.githubusercontent.com/ashesh2621/design-references/main/skills/INDEX.md | grep -iE "scroll|swipe|gesture|caption|text|reveal|carousel|video"
```
Target needs: (1) a **vertical swipe / snap-scroll** interaction for the reel, (2) an **animated text / word-reveal** skill for captions, (3) a **masked/staggered reveal** for the detail view entrance. Pick concrete slugs at build time against the brief's mobile, full-screen, sound-off requirements.
