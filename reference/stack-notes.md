# Stack Notes

**Why this doc exists:** Capture the version pins and "things future-you will forget" for News20's specific stack — especially the Capacitor-over-Next.js and Python→Remotion seams that don't exist in TLDW. Read before scaffolding or debugging build issues.

**When to update:** When a version is pinned/bumped, or when a gotcha bites and gets resolved.

## Version pins (inherit from TLDW unless noted)
- **Next.js** 15.3+, **React** 19.1+, **Tailwind** 4.1+ (`@tailwindcss/postcss`).
- **@trigger.dev/sdk** v4 (`task`, `schemaTask`, `schedules.task`). **Never** `client.defineJob` (v3, deprecated).
- **@supabase/supabase-js** 2.49+, **@supabase/ssr** 0.6+.
- **Python** 3.12+, **google-genai** ≥1.0 (Gemini TTS + Live), **pydub** ≥0.25, **pinecone** ≥5, **fastapi** ≥0.115, **structlog** ≥24.
- **Capacitor** — add `@capacitor/core`, `@capacitor/ios`, `@capacitor/cli` (latest 6.x+). NEW to this project.
- **Remotion** — `remotion` + `@remotion/cli` + `@remotion/lambda` (if rendering on Lambda). NEW.

## Capacitor + Next.js gotchas (NEW seam — not in TLDW)
- Capacitor ships a **static web bundle** inside the native shell. Next.js must build to a static export (`output: "export"`) **or** the shell loads a hosted URL. Decide early (master-plan Open Q4). Recommended: static SPA export + all dynamic data via Supabase client + a remote API on Vercel — keeps the binary thin.
- `output: "export"` disables Next.js server components / API routes / image optimization. Plan data flow around **Supabase-direct client calls** and standalone API endpoints, not Next route handlers baked into the binary.
- iOS WebView autoplay: muted autoplay is allowed; **audio autoplay needs a user gesture**. The reel's first tap unlocks audio — design the first-frame interaction accordingly.
- Mic for voice mode: declare `NSMicrophoneUsageDescription` in `Info.plist`; request permission via Capacitor before opening the Gemini Live WSS.
- `<video>` playback: use `playsinline` + `muted` defaults; preload the next 1–2 reel videos for seamless auto-advance.

## Gemini TTS gotchas (from TLDW `gemini_tts.py`)
- Model: `gemini-2.5-flash-preview-tts`. Multi-speaker = both voices in one call.
- Per-call output budget (~5000 bytes speakable text) → chunk on `<Person[12]>` boundaries, ~4000-byte budget with preamble headroom (TLDW already handles this).
- Internal labels stay **ALEX/JORDAN**; map to `Person1`/`Person2` only at the call boundary.
- Separate key supported: `GEMINI_API_KEY` + optional `GEMINI_API_KEY_TTS` (TLDW pattern).
- SynthID watermark is automatic — useful for the provenance/disclosure requirement.

## Gemini Live (voice mode — NEW for News20)
- Single low-latency streaming model (audio in/out) — no separate STT/LLM/TTS to stitch. 128K context (load full article + recent watch history).
- Function calling mid-conversation: fetch source article / related stories / stats on demand → wire these to the same RAG retriever used by typed Q&A.
- Proactive audio: respond only when speech is directed at it (critical hands-free on a commute).
- Ground every answer on the story's source set + run the `verification` stage; refuse when unsupported (Decision #5).

## Remotion gotchas (NEW)
- Renders are Node processes; the Python pipeline must hand off a render manifest (audio path, ordered image list + Ken Burns params, caption timing from `forced_alignment`, headline-card data). Use TLDW `tts_handoff.py` as the handoff template.
- Lock the 9:16 composition to the brief's structure: 0–2s hook, 2–8s stakes, 8–35s detail, 35–50s why-it-matters, 50–55s CTA/loop; 8–12 cuts total.
- Captions: white text, black outline, lower-middle third, one yellow-highlighted keyword/sentence — sound-off comprehension is a first-class requirement.
- Decide render runtime (worker vs. Remotion Lambda vs. Trigger task) — master-plan Open Q5.

## Logging / settings / lint (CLAUDE.md mandates, satisfied by TLDW ports)
- Structured JSON logs via `agents/shared/logger.py` (structlog) — include `fix_suggestion` on errors.
- Env via `pydantic-settings` (`agents/shared/settings.py`); never hardcode keys; keep `.env.example` current.
- Lint: Ruff (line length 120) for Python, Biome/Next lint for TS. Keep agent files <500 LoC, all files <1000.

## macOS one-time
Python HTTPS calls may need the SSL cert fix (`/Applications/Python 3.x/Install Certificates.command`) — noted in TLDW README quickstart.
