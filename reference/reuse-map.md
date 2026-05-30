# TLDW → News20 Reuse Map

**Why this doc exists:** The user's explicit directive is to limit new code by reusing the TLDW project, which shares most of News20's tech (Gemini multi-speaker TTS, grounded voice/chat Q&A, multi-source ingestion, RAG, Supabase + Trigger.dev). This doc is the authoritative inventory of what to copy/adapt vs. build new. `/plan-phases` and `/run-phase` MUST consult this before writing any agent, pipeline, or backend code.

**When to update:** Whenever a TLDW module is ported (mark it ✅ ported), whenever a "build-new" item turns out to have a TLDW analog, or if the TLDW source moves.

## Donor location

```
~/TLDW-Phase2/tldw/voice-agent-dashboard/
```
TLDW is "TL;DW Voice Agent Dashboard" — a daily two-host AI podcast generator (Alex + Jordan) with an interrupt-to-ask voice agent. Audio-only; News20 adds video + a swipe reel + a bias/trust layer on top of the same spine.

> Read TLDW's `master-plan.md`, `PRD.md`, and `CLAUDE.md` for its own architecture before porting. Do **not** edit files under TLDW `_legacy/`.

## Reuse decision per module

Legend: **PORT** = copy with minimal edits · **ADAPT** = copy then meaningfully change · **PATTERN** = copy the shape, rewrite the body · **NEW** = no TLDW analog, build fresh.

### Audio / TTS — the headline reuse
| TLDW path | News20 use | Decision |
|---|---|---|
| `agents/voice/gemini_tts.py` | Anchor-duo 55s narration (ALEX→`Leda`, JORDAN→`Sadaltager`, chunked multi-speaker calls, fallback pair) | **PORT** — this is the locked format; keep as-is |
| `agents/voice/audio.py` | Episode/segment assembly (`assemble_episode`) | **PORT** |
| `agents/voice/models.py` | Voice segment/turn models | **PORT** |
| `agents/voice/assets/generate_music_clips.py` | Ambient bed (~12 dB below voice, no trending tracks) | **ADAPT** |

### Script + caption timing + hallucination guardrail
| TLDW path | News20 use | Decision |
|---|---|---|
| `agents/pipeline/stages/scripting.py` | LLM condenses one source article → ~140-word two-person dialogue | **ADAPT** (retarget to 50–55s news digest, single-source constraint) |
| `agents/pipeline/stages/forced_alignment.py` | Word-level timing → word-by-word animated captions | **PORT** (drives Remotion caption track) |
| `agents/pipeline/stages/verification.py` | Hallucination guardrail — verify claims against source | **PORT** (also gates interrogation/voice answers, Decision #5) |
| `agents/pipeline/stages/ranking.py` | Story ranking / selection for the daily set | **ADAPT** (add interest-category weighting) |
| `agents/pipeline/stages/tts_handoff.py` | Hands script→audio→render; template for Python→Remotion handoff | **PATTERN** |
| `agents/pipeline/orchestrator.py` | Stage sequencing for the per-story job | **ADAPT** (insert image-source + Remotion stages) |
| `agents/pipeline/llm_clients.py`, `prompts.py`, `models.py`, `json_utils.py` | LLM client wrapper, prompt constants, typed models | **PORT** |

### Interrogation layer (the moat)
| TLDW path | News20 use | Decision |
|---|---|---|
| `agents/chat/agent.py` | Interrupt-to-ask agent → typed search-box Q&A + voice mode brain | **ADAPT** (ground on a single story's source set) |
| `agents/chat/prompts.py` | Q&A system prompts | **ADAPT** |
| `agents/rag/chunker.py` `embedder.py` `retriever.py` `pinecone_client.py` `pipeline.py` `models.py` | Grounding for Q&A so answers cite the source, not invent | **PORT** |

### Ingestion
| TLDW path | News20 use | Decision |
|---|---|---|
| `agents/ingestion/adapters/base.py`, `feed_utils.py` | Adapter interface + RSS/feed helpers | **PORT** |
| `agents/ingestion/adapters/youtube.py`, `podcast.py`, `personality.py` | (Out of scope for v1 — news only) | **SKIP for v1** (revive in later phase) |
| `agents/ingestion/dedup.py` | Cross-source story de-duplication → outlet-count + clustering | **PORT** (feeds "42 outlets covering this") |
| `agents/ingestion/scheduler.py`, `pipeline.py`, `processor.py` | Ingestion scheduling + orchestration | **ADAPT** |
| News adapters: NewsAPI, MediaStack, Alpha Vantage, Hacker News, Product Hunt | — | **NEW** (write against `base.py` interface) |

### Personalization
| TLDW path | News20 use | Decision |
|---|---|---|
| `agents/memory/player_signals.py` | Implicit signals (watch completion, swipe direction, dwell) | **ADAPT** (drives category prioritization) |
| `agents/memory/extractor.py`, `injector.py`, `session_processor.py` | Signal extraction/injection | **ADAPT** |

### Shared infra
| TLDW path | News20 use | Decision |
|---|---|---|
| `agents/shared/logger.py` | Structured JSON logging (CLAUDE.md mandate) | **PORT** |
| `agents/shared/settings.py` | `pydantic-settings` env management | **PORT** |
| `agents/shared/exceptions.py` | Custom exceptions (`TTSRenderError`, etc.) | **PORT** |
| `agents/shared/taxonomy.py` | Topic taxonomy → interest categories | **ADAPT** |
| `agents/worker/main.py`, `auth.py` | FastAPI worker entrypoint | **PORT** |
| `requirements.txt` | Python deps (google-genai, pydub, yt-dlp, pinecone, openai, fastapi, structlog…) | **PORT** (drop yt-dlp/youtube-transcript-api until later phase) |

### Frontend + backend scaffolding
| TLDW asset | News20 use | Decision |
|---|---|---|
| `package.json` (Next 15, React 19, Tailwind 4, radix-ui, framer-motion, zustand, sonner, supabase, trigger sdk, zod) | Base dependency set | **PORT** then add Capacitor + Remotion + a video player |
| `src/` (Next app, Supabase client/SSR, UI components) | App scaffolding, auth, design primitives | **ADAPT** (reel UI is new; auth/layout/client reuse) |
| `supabase/` migrations | DB scaffolding pattern | **PATTERN** (News20 schema is new: stories, digests, sources, bias, follows, signals) |
| `trigger/` + `trigger.config.ts` | Trigger.dev v4 task setup | **ADAPT** |
| `Dockerfile.web`, `Dockerfile.worker` | Container builds | **PORT** |

## Build-NEW (no TLDW analog)
- **Remotion video project** — composition (9:16, locked 0–55s structure, 8–12 cuts), Ken Burns image motion, animated word-by-word caption track, headline cards. (TLDW is audio-only.)
- **Image sourcing service** — Pexels / Unsplash / Pixabay fetch + AI still-image gen + Puppeteer headline screenshots + programmatic charts (Remotion/Lottie).
- **Swipe reel UI** — full-screen vertical auto-play, swipe up/right/left gesture routing (framer-motion), in-reel follow icon + headline-tap peek.
- **Story Detail View UI** — chunked readable text (<100s read), supporting visuals, bottom search box, sources sorted by bias.
- **Bias/trust layer** — static outlet→bias map (AllSides/Ad Fontes), coverage breakdown (L/C/R), outlet count, story timeline, blindspot rule (>70% one side), "read the opposing view".
- **Capacitor iOS shell** — native wrapper, mic permissions for voice, App Store build config.

## Porting rules
- Keep News20's verbose naming + structured-JSON logging conventions (`reference/conventions.md`) — TLDW already follows the same CLAUDE.md, so port cleanly.
- Don't blend TLDW's audio-episode model with News20's per-story video model where they conflict (Rule 7) — News20 is **one video per story**, not a twice-daily episode.
- When porting, copy the file, run `ruff` + tests, then adapt — don't hand-rewrite from memory.
