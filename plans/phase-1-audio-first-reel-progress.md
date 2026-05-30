# Progress: phase-1-audio-first-reel

**Phase file:** plans/phase-1-audio-first-reel.md
**Started:** 2026-05-29
**Phase-diff baseline commit:** 3a1da08 (M0 foundation)
**Execution mode:** SEQUENTIAL (linear deps SP1→SP2→SP3→SP4; SP3+SP4 share Reel.tsx)

## Sub-phase progress
- [x] 1: Scaffold SPA shell + design tokens + blip logo — COMPLETED (verified: out/ emitted, Vitest 8/8, Biome clean, tsc 0)
- [x] 2: Typed feed contract + M0 fixtures + karaoke selector — COMPLETED (verified: Vitest 29/29, tsc 0, Biome clean, fixtures landed)
- [x] 3: Reel surface — chrome + audio-driven karaoke + gestures — COMPLETED (verified: Vitest 35/35, tsc 0, Biome clean, out/ emitted; visual=PENDING human smoke)
- [x] 4: First-run audio unlock + finite-loop states — COMPLETED (verified: Vitest 61/61, tsc 0, Biome clean, out/ emitted)

## Phase-end checks (Step 3)
- **Phase DoD (automated): PASS** — static export serves index.html + all 3 real fixture types
  (audio/mpeg 1.2MB, image/png 2.4MB, application/json) at 200; full Vitest 61/61 covers
  getFeed→5 stories, captionState, advance, preload, status transitions. Biome clean (27 files),
  tsc 0. **Human smoke still owed:** "does the karaoke feel synced vs real audio?" (the judgment
  this phase exists to enable) — run `npm run dev` and eyeball digest-1.
- **Slop scan: PASS** — 1 explicit exception: `Digest` interface in src/types/feed.ts is unused
  by Phase 1 but kept intentionally as cross-phase contract documentation (doc-linked from Story;
  Phase 1b/3 getFeed materializes it). No console.log/TODO/any/localhost/dead-code elsewhere.
- **CSO: PASS — no security-relevant surface** (pure local-fixtures frontend): no secrets, no
  network calls, both catch blocks log, deps all mainstream/maintained.
- Orchestrator hygiene: gitignored next-env.d.ts + *.tsbuildinfo (Next convention); minimal
  additive README section for the new frontend toolchain.

## Resolved decisions (for sub-agents)
- **Caption token contract:** `word_tokens: [{word_text, is_highlight, start_ms, end_ms}]`
  per `supabase-schema.md:152` (authoritative) = phase file. port-map §3.1's
  `token_text`/`is_highlight_keyword` is superseded; do NOT use it.
- **M0 caption JSON shape (verified on disk):**
  `{digest_id, audio_duration_s, speech_end_s, sentence_count,
    words:[{word, start_s, end_s, sentence_index, is_highlight}]}` (seconds).
- **Vitest must scope to `tests/lib/**`** — Python pytest owns `tests/agents/**`.
- **Tokens are law** — tailwind.config.ts mirrors port-map §4 / prototype index.html.

## Notes
- news_digest_app_report.docx left untracked intentionally (excluded from baseline).
- SP1 added: vitest.config.ts, biome.json, src/app/page.tsx (placeholder — SP3 overwrites),
  `/out/` line in .gitignore. next-env.d.ts + tsconfig.tsbuildinfo untracked → orchestrator
  gitignores at phase end (Next convention). package-lock.json must be committed.
- SP2 added src/lib/logger.ts (justified shared infra, used in 2 files — NOT slop).
- SLOP-SCAN TODO: `Digest` interface in src/types/feed.ts is currently UNUSED (Story flattens
  digest fields). Decide at phase end: keep as contract doc-shape or wire Story→embed Digest.
- Selector signature: captionStateAtTime(caption_sentences, currentTimeMs, speech_end_ms) —
  pass speech_end_ms (NOT audio_duration_ms). Returns {current_sentence_index, current_sentence,
  current_speaker, words:[{word_text,timing,is_highlight,css_class_names}]}.
- Poster correction: genuine PNG 1080×1920 (phase file's "768×1376 JPEG" is wrong).
