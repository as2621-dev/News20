# Integrations

**Why this doc exists:** One place for every third-party API News20 depends on — auth pattern, rate limits, and what we use it for — so phases don't rediscover each one. Several are already wired in TLDW (`.env.example` there is the template).

**When to update:** When an integration is added, a key rotates, or a rate limit/quota bites.

## News ingestion (NEW adapters, against TLDW `ingestion/adapters/base.py`)
| Service | Use | Auth | Notes |
|---|---|---|---|
| **NewsAPI** | General news articles | API key (header/query) | Free tier limited + delayed; budget for paid tier at scale. |
| **MediaStack** | News articles, broad outlet coverage | API key (query) | Good for outlet-count breadth → "42 outlets covering this". |
| **Alpha Vantage** | Finance/markets news | API key (query) | 5 req/min, 500/day free — cache aggressively. |
| **Hacker News** | Tech stories | None (Firebase API) | No key; be polite with rate. |
| **Product Hunt** | Tech/product launches | OAuth2 (GraphQL API) | Token-based; refresh handling needed. |

All adapters implement the TLDW base adapter interface and feed `ingestion/dedup.py` for cross-source clustering (powers outlet count + coverage breakdown).

## Audio / voice (Gemini — reused from TLDW)
| Service | Use | Auth | Notes |
|---|---|---|---|
| **Gemini 2.5 Flash TTS** (`gemini-2.5-flash-preview-tts`) | Pre-generated anchor-duo digest narration | `GEMINI_API_KEY` (+ optional `GEMINI_API_KEY_TTS`) | Multi-speaker single call; ~5000-byte/call budget; SynthID auto. |
| **Gemini Live API** | Real-time hands-free voice mode | Gemini key | Streaming audio in/out, function calling, 128K context, proactive audio. |
| **OpenAI** | LLM for scripting / Q&A (TLDW uses it) | `OPENAI_API_KEY` | Confirm provider split vs. Gemini per stage. |

## Images / visuals (NEW)
| Service | Use | Auth | Notes |
|---|---|---|---|
| **Pexels / Unsplash / Pixabay** | Royalty-free stock stills for reel | API key each | Respect attribution + rate limits; cache by story. |
| **AI image gen** (provider TBD) | Story-specific stills (maps, abstract scenes) | API key | Still images only — no AI video (Decision #7). |
| **Puppeteer** | Headline screenshots from source outlets | None | Headless Chrome render; runs in worker. |

## Trust / bias (NEW — static, not an API)
| Source | Use | Notes |
|---|---|---|
| **AllSides / Ad Fontes Media** | One-time outlet→bias (L/C/R) lookup table | Build a static reference table once; no per-story API call. Powers coverage breakdown, blindspot (>70% one side), opposing-view shortcut. |

## Infra (reused from TLDW)
| Service | Use | Auth |
|---|---|---|
| **Supabase** | Postgres + auth + storage/CDN (serves MP4s) | URL + anon/service keys; `@supabase/ssr`. |
| ~~**Pinecone**~~ | ~~RAG vector store grounding Q&A + voice~~ — **DROPPED 2026-05-31.** Q&A/voice grounding loads the per-story corpus into the LLM context (verification-gated); the corpus is tiny (per-story, single-source) so no vector store is needed. See `plans/phase-2b-m2-grounded-interrogation.md` re-scope + master-plan Decision #5. | _(no key needed)_ |
| **Trigger.dev v4** | Once-per-story generation pipeline + ingestion schedule | Project key; v4 SDK only. |
| **Resend** | Transactional email (TLDW dep) | `RESEND_API_KEY` (optional v1). |

## Key handling
- All keys in `.env` (gitignored); maintain `.env.example` with placeholders (port from TLDW). Never log key values.
- Worker reads via `pydantic-settings` (`agents/shared/settings.py`).
