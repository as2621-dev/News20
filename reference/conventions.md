# Conventions

**Why this doc exists:** The single quick-reference for how News20 code is written, so every phase stays consistent. Derived from the global `~/CLAUDE.md` + project `CLAUDE.md` (14 rules), refined for this app. TLDW follows the same CLAUDE.md, so ported code already conforms.

**When to update:** When a convention is added or changed by team decision.

## Naming
- Verbose, intention-revealing, prefixed: `story_id`, `digest_mp4_url`, `signal_watch_completion_pct` — never `id`, `url`.
- TS: `PascalCase` components/types, `camelCase` functions/vars. Python: `snake_case` funcs/vars/modules, `PascalCase` classes, `UPPER_SNAKE_CASE` constants.

## File structure
- **Frontend:** `src/components/`, `src/lib/`, `src/types/`. Types mirror backend models exactly (see `api-contracts.md`).
- **Agents (Python):** modular by responsibility per the global CLAUDE.md layout — `agents/[name]/{agent,tools,prompts,models,dependencies}.py`, `agents/shared/`. Tools are pure typed functions.
- **Size discipline:** agent files <500 LoC, any file <1000 LoC. Split by responsibility.

## Logging (mandatory, structured JSON)
- Python: `structlog` via `agents/shared/logger.py` — `logger.info("digest_render_started", story_id=..., ...)`.
- TS: shared logger util — `logger.info("fetch_success", { total })`.
- snake_case event names; include contextual fields; **every error log includes `fix_suggestion`**.

## Types & validation
- TS strict mode, no unjustified `any`. Python type hints everywhere; Pydantic v2 at all API boundaries (never raw dicts).
- Forms: React Hook Form + Zod. Backend response shapes match `api-contracts.md`.

## Error handling
- Standard `ErrorResponse` shape (see `api-contracts.md`). Python raises typed exceptions from `agents/shared/exceptions.py`, serialized at the boundary.
- Fail loud (Rule 12): never report "done"/"tests pass" if anything was skipped.

## The 14 project rules (project CLAUDE.md) — load-bearing reminders
- **R2 Simplicity / R3 Surgical:** minimum code, touch only what's needed, no speculative abstractions. For News20 this especially means **reuse TLDW before writing new** (`reuse-map.md`).
- **R7 Surface conflicts:** don't blend TLDW's audio-episode model with News20's per-story video model — pick one, flag the other.
- **R8 Read before write:** read the TLDW source module before porting; run its tests after copy.
- **R11 Match conventions:** conform to the codebase even if you'd choose differently.
- **R13 Anticipate next step:** every response ends with the concrete next action.

## Testing (when requested)
- Mirror structure: `test_<module>.py` / `test_<module>.ts`. Mock all external services (LLM, TTS, news APIs, DB) at the boundary.
- Per new function: happy path + failure case + edge case. Tests encode *why* (R9), not just behavior.

## Lint / format
- Python: Ruff (line length 120), `ruff check --fix` + `ruff format`. TS: Biome / `next lint` (120 char, double quotes per global rules). Must pass before a phase completes.

## Secrets
- All keys in `.env` (gitignored); maintain `.env.example`. Never hardcode or log key values. Worker uses `pydantic-settings`.
