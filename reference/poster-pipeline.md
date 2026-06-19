# Poster Pipeline — SSOT for News20 story-image generation

**Date:** 2026-05-29 (revised after the M0 spike). **Status:** Single source of truth. **Owns:** every story visual in News20.

**This revision OVERRIDES the earlier image-prompt strategy in this same doc** (the pre-spike "concept-first *illustration/caricature*" method). The validated, locked approach is **SERP-seeded, concept-first, photoreal-graphic** generation, defined in §1–§9 below. Earlier archetype/metaphor research is retained as *secondary inspiration* in §10; provenance in §12–13.

**Companion:** `design-language.md` owns the app UI (player, captions, tokens). This doc owns the *generated image* and defers to design-language tokens for palette/fonts.

> The running implementation is `agents/m0/` (concept → search → score → synthesize → generate → grade). **This doc is the spec that code implements; if they ever disagree, reconcile them.** Plan: `plans/phase-0b-serp-seeded-poster-pipeline.md`.

---

## 1. The principle (one line)

> **Find the real photo, then recast it with AI into a photoreal-graphic poster that tells the story at a glance — one subject, one accent, the right emotion, and a player-aware safe zone.**

Validated on the 5 M0 stories. Pure concept-illustration produced clean but *generic* metaphors with no grounding in what the story looks like; seeding from a real, top-ranked news photo gives true subject likeness + an iconic composition, which AI then transforms (a little metaphor, a little graphic) into our brand. The user locked this register from `assets/m0/digest-1/variant-b-photoreal.jpg`.

**Seven laws (kill conditions if violated):**
1. **Story legible at a glance.** A viewer who can't read any text must grasp the story from the image alone — show the **key subject WITH the defining object/action**. If not → regenerate.
2. **One main subject.** Exactly **one person** as the focal character — **no incidental background people, co-workers, aides or bystanders** (the generator must drop anyone else from the seed photo). Two+ people ONLY when genuinely central (a confrontation/partnership, e.g. two named leaders). Driven by the concept's `central_subject_count`.
3. **One accent, but colour serves meaning.** Near-black `#020617` + ONE segment accent per image — with the **sole exception** that any element encoding direction/sentiment uses its **semantic colour: green = up/gain, red = down/loss**, even if that differs from the segment accent. *A falling stock line is RED, never green.* No other two-colour compositions. Driven by the concept's `directional_sentiment`.
4. **Player-aware safe zone.** The subject lives in the **upper ~3/4**; the **lower ~40%** stays quiet/dark for the caption + player-control overlay (§2). Never place critical content where the player UI sits.
5. **Photoreal base + graphic treatment + ONE subtle metaphor.** Recognizable real likeness, then bold poster/graphic styling + the duotone grade. **NOT a flat cartoon/caricature.**
6. **Concept-first + seed-grounded.** The concept (subject, defining object, true emotion, gist) is decided in words; the seed is found by search; the image is *transformed*, never copied.
7. **The image carries the idea wordless.** Headline / trust tags / buttons / captions are a separate overlay layer; the generated image is textless.

---

## 2. Format, the fixed frame & the player-aware safe zone

- **Canvas:** 9:16 vertical, **1080×1920**, base `#020617`.
- **The image is a background.** Headline + trust tags + buttons + word-by-word captions are a **separate overlay** composited on top (the inviolate brand frame: logo top, captions lower-middle third, player controls bottom). The image varies infinitely beneath it; the frame never moves — the #1 coherence lever.
- **Safe zone (player-driven, LOCKED 2026-05-29):** anchor the subject in the **upper ~3/4** of the frame. Keep the **lower ~40% low-detail and dark** — that band carries the sound-off captions (lower-middle third, per `design-language.md`) and the player controls, and a deterministic scrim (§9) darkens it further. The current M0 posters do exactly this and the look is approved.
- **Title-safe inset (SMPTE/Netflix):** keep all overlay glyphs + button hit-targets inside the inner 90% → ~**54px** L/R, ~**96px** top/bottom keep-out.
- The generator is told the safe zone explicitly in the synthesis prompt + the house suffix ("anchor subject upper/center; reserve the lower 40% as quiet dark negative space").

---

## 3. The pipeline (end-to-end, as implemented in `agents/m0/`)

| # | Stage | Module | Engine |
|---|---|---|---|
| 0 | **Concept** (§4) | `story_concept.py` | Gemini `gemini-2.5-flash` (JSON) |
| 1 | **Search** (query from concept, `num=10`) | `serper_image_search.py` | Serper.dev Google Images (`SERPER_API_KEY`) |
| 2 | **Gate** (dedup URL · drop tiny <320px · drop watermark-stock domains) | `serper_image_search.py` | deterministic |
| 3 | **Download** top 5 (full image, fallback thumbnail) | `download_candidates.py` | httpx + Pillow |
| 4 | **Score** 6 criteria (§5) | `image_scorer.py` | Flash multimodal (JSON) |
| 5 | **Select** (weighted total + relevance gate + tie-breaks) | `image_scorer.py` | deterministic |
| 6 | **Synthesize prompt** (§6) | `reference_prompt_synthesizer.py` | Flash multimodal |
| 7 | **Generate** (image-conditioned on the winning seed + prompt, textless) | `generate_posters.generate_from_reference` | **Nano Banana Pro `gemini-3-pro-image-preview`** |
| 8 | **Grade & brand** (§7 + scrim) | `grade_and_brand.py` | deterministic (Pillow) |

Every story also writes `assets/m0/<digest>/selection-report.json` (query, all candidate scores, winner, synthesized prompt) for audit (Rule 12 — never silently pick). All judgment steps use the cheap flash model; only the final render uses Nano Banana Pro.

---

## 4. The concept step (Gemini Flash → JSON)

Given headline + summary, emit (`StoryConcept` in `poster_models.py`):

```
image_search_query       — 3-8 words to surface a strong real EDITORIAL photo of the subject
                           (prefer named people/places, ideally WITH the defining object/action)
key_subject              — the single most important person/entity to SHOW (prefer the named person)
defining_object_or_action — the ONE object/action that makes the story legible at a glance
emotional_valence        — the TRUE mood, INCLUDING irony (great results but stock fell → subdued,
                           NOT triumphant)
gist                     — one sentence a viewer should grasp from the image alone
is_person_driven         — bool
central_subject_count    — # people CENTRAL to the story (1 normally; 2 only for a real
                           confrontation/partnership). Bystanders/co-authors don't count → law 2
directional_sentiment    — up_gain | down_loss | none; sets the semantic colour of any
                           trend element (green up / red down) → law 3
```

This concept drives the search query, the scoring emphasis, AND the synthesis prompt — so every downstream step optimizes for "tell the story at a glance, with the right emotion."

---

## 5. The selection rubric (6 criteria — LOCKED weights, user 2026-05-29)

The chosen image is a **seed to transform**, so criteria measure what the seed uniquely supplies (orientation/accent/grade are imposed at generation, so they're de-weighted). Each candidate scored 0–10:

| # | Criterion | Weight | Judges |
|---|---|---|---|
| 1 | **Headline aptness** | **×2** | Depicts the key subject AND ideally the defining object/action — story legible at a glance? Reward "tells the story"; penalize pretty-but-generic. |
| 2 | **Metaphor / transformation potential** | ×1.5 | If recast, could it become a strong conceptual poster? |
| 3 | **9:16 vertical fit** | ×1 | Does the focal arrangement survive a vertical recompose? |
| 4 | **Single dominant + recognizable subject** | ×1 | One clear, identifiable hero (ideally the key subject)? |
| 5 | **Iconic distinctiveness** | ×1 | Striking & memorable vs. a generic wire/stock photo? |
| 6 | **Emotional tone match** | **×3** | Does its mood match the concept's `emotional_valence`? |

**Weighted total** = `2·c1 + 1.5·c2 + c3 + c4 + c5 + 3·c6` (max 95). **Relevance soft-gate:** `c1 < 5` ⇒ disqualified before ranking (so a striking-but-off-topic image can't win); if *all* fail, every candidate stays eligible so we still return one. Tie-break: headline_aptness → iconic. Weights live in one place: `poster_models.SELECTION_CRITERIA`.

---

## 6. The synthesis prompt (THE prompt — what actually generates the image)

The winning photo + the concept go to Gemini Flash, which writes a **recast** prompt under these locked requirements (`reference_prompt_synthesizer.py`):

- **PHOTOREALISTIC base** — believable, recognizable real likeness of the subject (NOT a flat cartoon/caricature), then layer **bold GRAPHIC poster treatment** (cinematic lighting, strong duotone grade, subtle graphic shapes/texture). Think *photoreal movie-poster key art*.
- **TELL THE STORY AT A GLANCE** — show `{key_subject}` together with `{defining_object_or_action}`; story gist `{gist}`.
- **ONE MAIN SUBJECT** — depict exactly `{central_subject_count}` person(s); drop all other bystanders/background figures from the seed (law 2).
- **ONE restrained visual metaphor** only if it strengthens the idea.
- **EMOTIONAL TONE** = `{emotional_valence}` exactly (the subject's expression + the whole mood must match — e.g. subdued/ironic, not triumphant).
- **COLOUR SERVES MEANING** — segment accent `{accent_hex}` on the subject, BUT any up/down trend element uses its semantic colour (green up / red down) per `{directional_sentiment}` (law 3).
- **SAFE ZONE** — subject upper/center; lower 40% quiet.
- Direct generation instructions; **do NOT describe a literal copy of the seed photo.**

A deterministic **house suffix** (`poster_prompts.HOUSE_RENDER_SUFFIX`) is then appended to guarantee the brand constants: *"Near-black background #020617, single accent hue only, duotone grade toward #020617 plus the accent, cinematic single-source rim light, deep shadow falloff into black, fine film grain, soft radial vignette toward the focal point. High contrast, one clear idea, legible at 120px thumbnail. Reserve the lower 40% of the frame as quiet dark low-detail negative space for a caption overlay; 9:16 vertical. --no text, no logos, no watermark, no border, no UI, no words."*

**Worked example — digest-1 (the actual generated prompt):**
> *"A dramatic, photorealistic movie-poster key art of Donald Trump, centered in the upper third of a 9:16 vertical poster, his expression tense and determined… a photorealistic, stylized map of the Strait of Hormuz… critical shipping lanes subtly highlighted with an internal, contained glow of #EF4444… The lower 40% of the poster is quiet, dark, and textured…"* + house suffix.

**Worked example — digest-4 (Nvidia, corrected emotion):**
> *"…hyperrealistic movie-poster key art of Nvidia CEO Jensen Huang… a contemplative, somewhat subdued expression. A stylized, downward-trending stock chart line… descends into the lower frame, suggesting a peak and subsequent decline despite apparent success… accent #22C55E…"* + house suffix.

---

## 7. Register & house treatment (the constant that makes them all "us")

- **Register = photoreal base + graphic poster treatment + ONE subtle metaphor.** This **supersedes** the old "editorial-illustration / caricature default." The M0 spike confirmed Nano Banana Pro does **not** refuse photoreal named living figures (Trump, Khamenei, Huang, the Pope all generated), so photoreal-with-graphic-styling is the default. Flat caricature/illustration is now an *option a specific story can lean into*, not the house default.
- **Grade pass (deterministic, every image — the consistency lock):** cover-fit to 1080×1920; subtle desaturate; pull shadows toward `#020617`; single-accent edge vignette; lower-~40% scrim; faint film grain. **Subtle + consistent** — over-grading reads as a cheap filter. Implemented in `grade_and_brand.py`.
- **Lighting:** single hard-ish directional key on the subject; chiaroscuro against the dark field; soft accent glow ("lit-from-within").
- **Typography (overlay only — defer to `design-language.md`):** Inter (display) + Playfair Display (serif headline) + JetBrains Mono (trust stats). **Bold, never thin.**

---

## 8. Segment accent palette (exactly one accent per poster)

Story-driven first (Rise/Fall → green↑/red↓; Versus → the contested color), else the segment default. Must NOT collide with UI-reserved `#3B82F6` (actions) or `#FACC15` (caption highlight). Implemented as `poster_models.SEGMENT_ACCENT_BY_DIGEST`.

| Segment | Accent | Hex |
|---|---|---|
| Geopolitics / conflict | ember red | `#EF4444` |
| Markets / company | green | `#22C55E` |
| Tech & science | cyan | `#22D3EE` |
| Sport | amber | `#F59E0B` |
| Diversified / wildcard | cyan (AI-leaning) or blush | `#22D3EE` / `#E8B7BC` |

---

## 9. Legibility & QA gates (both required before a poster ships)

1. **Semantic split-second gate** (meaning): mask the overlay; ask a fast vision model *"in one sentence, what is happening / what is the single subject?"* Ambiguous or names >1 subject → regenerate. *(This is the law-1 check; the multimodal scorer already pushes toward it, but run it on the final poster too.)*
2. **WCAG brightest-pixel gate** (contrast): composite the scrim, sample the brightest pixel in the text band, require ≥4.5:1 against white; deepen the scrim until it passes.

**The scrim (deterministic):** bottom vertical gradient transparent → `#020617` (~85% opacity) over the lower ~40%; gradient length ≈ 3× the bar height (no OLED banding); + a subtle full-frame vignette. Built in `grade_and_brand.py`.

**What to AVOID:** incidental background people / a second figure that adds no information (law 2); a directional element in the wrong colour — a *falling* chart rendered green, a *rising* one red (law 3); more than one accent beyond that semantic exception; cluttered crowd/podium scenes; baking text into the image; an emotion that contradicts the story (triumphant when the market punished it); letting the subject fill the whole frame (always keep the lower ~40% quiet).

---

## 10. Metaphor toolkit (secondary inspiration — no longer deterministic routing)

The earlier 6-archetype routing is **demoted** to an idea bank the synthesis step can draw on when adding a metaphor. The primary driver is the concept + the real seed, not a forced archetype.

| Archetype | When to lean in | Mood lever |
|---|---|---|
| Versus / Split | 2 named adversaries | vertical seam = standoff; the seam's negative space can form a third object (missile, trophy, falling chart) |
| Single Icon | one concrete event/object | calm, authoritative |
| Metaphor / Pun | abstract concept, no natural photo | the "smart" one |
| Rise / Fall Arrow | the story IS a number/trend | green ↑ / red ↓ |
| Hero Portrait | one dominant person | monumental |
| Surreal / Grim | war, disaster, tragedy | subject emerges from / dissolves into black |

Metaphor families to mine: world-as-object, leader-as-object, national-animal, fragile-object (bull on balloons, house of cards), object-as-weapon pun, scale/balance/game, substitution/rebus, rise-fall trajectory, monster/threat. The two most lethal for us remain the **object-as-weapon pun** and the **leader/national-animal symbol**.

---

## 11. Motion — static first

Ship **static**. Spotify Canvas + Apple Music both warn that motion *under text* is the failure mode. **Later phase only:** a slow seamless loop confined to the **upper/background region** (parallax drift, grain shimmer, slow light) with the **lower text band perfectly still**. Spec: 9:16, 1080×1920, 3–6s loop, ≤8MB, 24–30fps.

---

## 12. Open items

- [x] **Generation spike (was §5 caricature):** RESOLVED — Nano Banana Pro renders recognizable photoreal named figures without refusal; photoreal-graphic locked as default.
- [ ] **IP / right-of-publicity:** the pipeline conditions generation on a real (often copyrighted) news photo → derivative-work question for a commercial app. Fine for the internal M0 spike; resolve before ship (bias scorer toward editorial/CC sources, or hold the seed as text-described inspiration rather than an image-to-image input).
- [ ] **Small-thumbnail likeness:** when full-image hosts 403, we fall back to a ~200px thumbnail seed (e.g. digest-5 Pope), which weakens likeness fidelity. Lever: a Referer header to beat more 403s, or prefer larger fetchable seeds.
- [ ] **Finalize segment accents** (§8) + contrast-check on `#020617`; add tokens to `design-language.md`.
- [ ] **Unit tests** for the pipeline modules (built for validation first; tests deferred).
- [ ] **Reconcile `documents/m0-digests.md`** (still describes the old 8-cut + Ken Burns format).

---

## 13. Named creatives (provenance)

- **News covers:** TIME (D.W. Pine; Edel Rodriguez; Platon) — red border, design by subtraction. The Economist (Kenny, Petch, Berkeley, D'Urbino, KAL) — single-metaphor benchmark, concept-first. The New Yorker (Mouly; Blitt, Nelson, Niemann) — image-only, black-on-black 9/11. Bloomberg Businessweek (black+amber dataviz), CNN (bold-beats-thin), Reuters/AP (decisive-moment ethics).
- **Cinematic geopolitics:** Caspian Report, Johnny Harris / Vox Borders, RealLifeLore, Wendover, PolyMatter.
- **Poster/illustration masters:** Saul Bass, Noma Bar, Malika Favre, Olly Moss, Christoph Niemann, Shepard Fairey, the Polish Poster School, Soviet Constructivism, key-art studios (Gravillis, BLT), Swiss/International Typographic Style.

## 14. Sources (load-bearing)
- **Covers:** en.wikipedia.org/wiki/List_of_covers_of_Time_magazine_(2020s) · imd.org/ibyimd/creativity/time-magazines-iconic-covers · magculture.com/blogs/journal/stephen-petch-the-economist · itsnicethat.com/features/francoise-mouly · tabletmag.com/sections/news/articles/art-spiegelmans-911-new-yorker-cover
- **Craft / pipeline:** partnerhelp.netflixstudios.com (title-safe) · m1.material.io/style/imagery.html (scrim) · smashingmagazine.com/2023/08/designing-accessible-text-over-images-part1 · 99designs.com/blog/trends/duotone-design · blog.google/innovation-and-ai/products/nano-banana-pro · serper.dev (Google Images SERP)

## 15. Canonical reference-image store (phase-0c — identity grounding)

**Problem it fixes:** today identity is sourced from the image model's training prior, so a *role* story ("Fed chair", "G7 leader") renders the former-but-famous incumbent (Powell, Biden) instead of the person the story actually names. The fix: resolve WHO from the **story text** (the name) and condition the image model on a **verified, current canonical photo** (the face) — never let the model decide who a person is. See `plans/phase-0c-poster-identity-grounding.md`.

**The store (migration `0019_entity_reference_images.sql`):**

- **Table `entity_reference_images`** — one row per resolved person, holding the VERIFIED current reference photo used to condition Nano Banana Pro:
  - `reference_id` (uuid pk), `entity_key` (text, **unique**), `entity_kind` (text, default `'person'`), `reference_storage_path`, `reference_public_url`, `source_page_url`, `verified_at`, `valid_as_of` (date), `verification_confidence` (real, 0–1), `created_at`, `updated_at`.
  - **`entity_key`** is the **normalized resolved person name** — lowercased + trimmed (e.g. `donald trump`). It is the demand-driven lookup key: SP3 upserts by it, SP4 reads by it. UNIQUE ⇒ one canonical photo per resolved entity; a re-fetch upserts the same row. It is computed in code, NOT an FK to the `entities` registry (0007) — resolution is demand-driven from story text, not the static catalog.
  - **RLS:** public-read (anon `select using (true)`), service-role-only write (no INSERT/UPDATE policy) — same shape as `entities` (0007) and the content tables (0002).
- **Bucket `entity-reference-images`** (public read) — holds the uploaded verified reference photos. Created idempotently in the migration via `insert into storage.buckets`, mirroring `digest-audio` / `story-posters` (0002), with a public-read object policy.

**What it holds:** VERIFIED, *current* reference photos — accepted only after a Flash identity-verification pass (SP3) confirms the face is the named person and a recent likeness (not a former office-holder), at `verification_confidence` above threshold. When no verified photo exists for a resolved person, the pipeline behaves exactly as today (the best-SERP-seed path, §1–§9) — this store only stops the model from *guessing* once a trusted photo is held.
