# Project Template

A slim Claude Code template. 12 commands, 14 rules. No fluff.

> **New here?** Read [`QUICKSTART.md`](./QUICKSTART.md) first — the 5-minute, plain-English version of what this does and how to use it. Come back here for the full details.

## blip — the app in this repo

This repo builds **blip** (codename News20): an audio-first AI news-digest iPhone app. **Phase 1** ships the Next.js 15 static-export reel that plays the 5 real M0 digests as local fixtures — audio-driven karaoke captions, a finite swipe loop, and the "all caught up" finish line. No backend yet (fixtures only; Supabase lands in a later phase).

```bash
npm install        # frontend deps
npm run dev        # Next dev server → http://localhost:3000
npm run build      # static SPA export → out/
npm test           # Vitest unit suite (tests/lib/**)
npm run lint       # Biome
```

Cross-phase feed contract: `src/types/feed.ts`. Reel UI: `src/components/reel/`, `src/lib/reel/`. Karaoke selector: `src/lib/captions/captionState.ts`. Phase record: `plans/phase-1-audio-first-reel-progress.md`.

**Feed-source revamp (2026-06-30, `plans/prd.md`).** News is a shared backbone; personalization comes from the YouTube channels / X accounts / Personalities you follow (interests collapse to 8 top-level roots; clusters bulk-select sources; followed items lead the feed). Followed-source items are summarized by content length via a **code-side long-vs-short summary selector** (`agents/pipeline/summary_mode.py`): a `youtube.com` long-form video/podcast gets a **key-points** summary, an `x.com` tweet/short clip a **tight** one, and news stories keep today's prompt unchanged — **prompt-only, no new ingestion or schema**. See `reference/ranking-spec.md` and `personalization-and-source-curation-spec.md`.

Karaoke caption timing is produced server-side by **acoustic forced alignment** (`agents/pipeline/stages/acoustic_alignment.py` — offline torchaudio Wav2Vec2 CTC, no paid API; requires `torch`/`torchaudio`/`num2words` from `requirements.txt`, ~360MB model auto-downloaded to the torch cache on first run). If those deps are absent the pipeline falls back to heuristic char-weight slicing (`forced_alignment.py`).

### Environment variables

| Variable | Default | What it does |
|---|---|---|
| `NEXT_PUBLIC_VOICE_CORPUS_IN_CONTEXT` | off | Live-voice grounding mode. **On** (`1`/`true`): the story's full grounding corpus is injected into the voice session so the model answers corpus-answerable questions directly. **Off** (default): legacy path — every factual question is forced through the `ask_about_story` tool round-trip. The trade-off is latency (on is faster) vs. grounding strictness (off keeps the server's two-guardrail verification). |

> The sections below document the **Claude Code template** this project was scaffolded from.

## Use this template for a new project

This repo is configured as a **GitHub Template Repository**. Don't fork — use the template button.

1. On GitHub: open the repo page → click **"Use this template" → Create a new repository** → name it (e.g. `acme-app`) and create.
2. In Cursor / VS Code: **New Window → Clone Repository →** paste the new repo's HTTPS URL.
3. In the new project root:
   ```bash
   cp .env.example .env   # fill in real secrets — .env is gitignored
   ```
4. Open Claude Code in that directory and run:
   ```
   /ideate "<focus area>"   # optional — only if you have no idea yet
   /brainstorm "<your rough idea>"
   /cto
   /to-issues
   /grab-issue
   ```

You now have a fresh repo with its own git history, all rules in `CLAUDE.md`, all 12 commands in `.claude/commands/`, and empty `plans/`, `reference/`, `documents/`, `.agents/` ready to fill.

> **Heads up:** `.claude/settings.local.json`, `.env`, and `.cursor/rules/openmemory.mdc` are all gitignored — they're machine-local. The shared, version-controlled config is `.claude/settings.json` (if present), `.env.example`, and everything in `.claude/commands/`.

## The 14 rules

See [`CLAUDE.md`](./CLAUDE.md). These apply to every task.

## The 12 commands

The first five are the core pipeline (top-to-bottom); the rest are support commands.

| Command | Use it to… |
|---|---|
| `/ideate` | **Optional, for a blank page.** When you have no specific idea yet: research → ideas from many angles → adversarial cut → ranked shortlist. Hand the winner to `/brainstorm`. |
| `/brainstorm` | Pressure-test a raw idea with a relentless, **non-technical** interview AND refine it into a product brief. Critical — pushes back, won't flatter. |
| `/cto` | Turn the product brief into a **PRD** (with a Technical Foundation section) + reference docs. |
| `/to-issues` | Slice the PRD into vertical-slice (tracer-bullet) issues on the GitHub kanban backlog. |
| `/grab-issue` | **Dispatcher** — pull the top unblocked slice and hand its build to a **fresh sub-agent** (clean context per slice; `/loop /grab-issue` drains the backlog without context rot). The sub-agent builds it **test-first (red→green→refactor)**: test → code → simplify → slop scan → CSO → acceptance → browser-verify UI (puppeteer + browser-use) → single commit → **multi-agent review panel** → done. |
| `/improve-architecture` | Every few days. Find shallow/tangled modules, propose deepenings in plain language, file refactor slices, and sync the PRD Technical Foundation + reference docs. Runs proactively inside `/cto` on re-runs. |
| `/office-hours` | Run a weekly diagnostic. What's stuck, what's risky, what's the next call. |
| `/rca` | Root-cause analysis for a bug. Diagnoses + proposes a fix. Doesn't apply it. |
| `/debug` | Autonomous browser bug hunt. Reproduces with `browser-use`, diagnoses with Chrome DevTools, fixes, re-verifies in-browser, loops until gone. Applies the fix; hands off to `/commit`. |
| `/commit` | Conventional commit. Stages explicit files. Never amends. Never skips hooks. |
| `/codex` | Adversarial second opinion. The 200-IQ pedant. Use when stuck or want pushback that doesn't social-smooth. |
| `/handoff` | Compact the conversation into a handoff doc (saved to temp, not committed) so a fresh agent can continue. |

## Typical flow for a new initiative

```
/ideate "focus area"                   → documents/ideation-*.md (optional — ranked directions for a blank page)
/brainstorm "rough idea"               → documents/product-brief.md (non-technical stress-test + refined brief)
/cto                                    → plans/prd.md (Technical Foundation + user stories) + reference/*.md
/to-issues                              → vertical-slice issues on GitHub (status:backlog)
/grab-issue                             → fresh sub-agent builds slice → simplify → slop/CSO → browser-verify UI → 1 commit → review panel → status:done
/loop /grab-issue                       → drain the backlog one slice at a time (fresh context each → no rot)
/improve-architecture                   → every few days: deepening RFCs + synced PRD/reference docs
/office-hours                           → weekly check-in
/rca "thing X broke"                    → .agents/rca/*.md (when bugs happen)
/debug "checkout button does nothing"   → .agents/debug/*.md (browser bugs, auto-fixed)
/codex challenge <diff>                 → when you want adversarial pressure
/handoff                                → hand off mid-stream to a fresh session
```

## The kanban board

Slices live on **GitHub Issues**, not in the repo. The board is just labels:

`status:backlog` → `status:in-progress` → `status:review` → `status:done`

Plus `ready-for-agent` (fully specced, grabbable), `blocked` (has an open blocker), and `slice` (a tracer-bullet issue). `/grab-issue` moves a card across columns as it works it, and unblocks dependents when a slice closes.

**To see it as a board:** open the repo's **GitHub Project** (Projects tab) — `/to-issues` adds each slice to it with a Status field that mirrors the `status:*` labels. Native drag-and-drop columns, nothing to host. Each slice is sized to be built by one subagent within a **120k-token** budget.

## What `/grab-issue` does to ship a slice

`/grab-issue` is a thin **dispatcher**: it picks one unblocked slice and hands the whole build to a **fresh sub-agent** with its own 120k-token context, so a long `/loop /grab-issue` never accumulates context rot. That sub-agent runs the slice end-to-end:

1. **Plan** — prior learnings (`docs/solutions/`), edge-case enumeration, conditional risk lenses (security / perf / migration / data / architecture / git-history)
2. **Build test-first** — red→green→refactor, one behavior at a time; UI behaviors also get a committed **puppeteer** regression test
3. **Simplify** — a `/simplify` quality pass (reuse / efficiency / altitude), pre-commit
4. **Slop scan** — vacuous comments, `any` casts, defensive try/catch, dead code, marketing voice, hardcoded `localhost`, leftover TODOs
5. **CSO lite** — secrets, auth boundary changes, input validation gaps, injection surface, new dependency health, log hygiene
6. **Acceptance check** — each acceptance-criteria checkbox on the issue actually holds (a real check, not "it compiles")
7. **Browser walkthrough (UI slices)** — drive the real acceptance flow with **browser-use** in a live browser; not done until it passes
8. **Commit + multi-agent review panel** — one commit, then a Claude-native panel of independent reviewer sub-agents (correctness + simplicity always; security/perf/contract/data when the diff fires them) behind a defer-vs-fix residual gate

Critical/high findings are fixed before done; deferred medium/low land in a durable sink (`.agents/cso-findings/`, a residuals file, or a new slice issue) — never dropped.

## Directory layout

```
CLAUDE.md                          # The 14 rules
.claude/commands/                  # The 12 slash commands
documents/                         # Ideation shortlists + product briefs (ideate + brainstorm output)
plans/                             # PRD with Technical Foundation (CTO output)
reference/                         # Stack notes, conventions, API contracts, design language (CTO output)
  └── browser-debug-playbook.md    # Browser tooling: /debug routing + UI-slice puppeteer/browser-use (§7)
design-references/                 # Pointer only — full library is remote
  └── RESOURCES.md                 # Points to github.com/ashesh2621/design-references
                                   # (86 skills + 511 design systems + 2,827 components
                                   #  + 20,660 templates, ~1 GB, fetch on demand)
.agents/
  ├── office-hours/                # Weekly diagnostic notes
  ├── rca/                         # Root-cause analyses
  ├── debug/                       # Browser debug reports from /debug
  ├── codex/                       # Codex transcripts
  └── cso-findings/                # Deferred medium/low security findings
```

Slice work itself lives on GitHub Issues. Handoff docs go to the OS temp / scratchpad, never committed.

## Notes

- `/grab-issue` and `/debug` are the only commands that touch feature code (`/grab-issue` builds slices; `/debug` applies a verified bug fix). Everything else writes docs, plans, or reports.
- One commit per slice. The issue + its labels are the checkpoint, so an interrupted slice resumes cleanly.
- Each slice is a **thin vertical path** through every layer (data → logic → UI → tests) — demoable on its own. Keep them scoped tightly enough that an agent can execute one given only the issue and `CLAUDE.md`.
- `/codex` is **user-triggered only**. `/grab-issue` does NOT auto-invoke Codex on findings — humans decide when to escalate.
