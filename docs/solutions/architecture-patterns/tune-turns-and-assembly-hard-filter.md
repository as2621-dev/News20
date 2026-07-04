---
title: TUNE turns degrade (not retry) + mutes as an assembly-level hard filter
tags: [interview, tune, mutes, feed-assembly, hard-filter, llm-fallback, stateless-turn]
problem_type: pattern
symptoms: adding LLM-worded onboarding turns that must never dead-end; per-user "mute" that must mean gone-not-demoted
date: 2026-07-04
---

Two reusable patterns from FSR slice #17 (interview TUNE turns + mutes), commit `4b4fd34`.

**1. LLM-worded turn that DEGRADES to deterministic copy instead of retrying.**
The shipped sub-niche turn returns a typed `retry` on any Gemini failure (the client re-asks).
That is wrong for a *code-enforced* turn whose JOB must always be served (founder-locked SKIP).
Pattern: the deterministic phase machine (`agents/interview/phases.py`) always emits the turn;
the LLM only supplies wording + option labels; on timeout/malformed/unusable output the engine
(`_run_tune_turn`) returns a deterministic **fallback question** (fixed copy + generic options),
never a retry. Traceability is preserved because the recorded answer is still the user's real
tap/typed text (`real_taps` excludes the skip/type meta affordances), and engine-provided
fallback options are legitimate taps (same as the fixed root bubbles). Test the failure path by
raising at the `call_gemini_json` boundary and asserting `response_kind == "question"`.

**2. Per-user "mute" = HARD FILTER at assembly, never at ingestion, never downranking.**
The story pool is SHARED across users, so a per-user filter cannot touch ingestion. Apply it in
`assemble_niche_feed` / `assemble_user_feed` (`agents/pipeline/feed_assembly.py`) by removing
matching stories from a COPY of the pool BEFORE any pass (and before the niche→coarse routing, so
both paths inherit the clean pool). Gotchas that bit / were avoided:
- **Word boundaries, not substring:** a naive `"india" in title` drops "Indiana"; a 2-char "ai"
  nukes "Spain". Use one escaped alternation with lookarounds: `re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.I)`.
  Escaping also kills regex-injection from a typed mute term.
- **Never mutate the shared input list** — filter into a new list (a sibling user who didn't mute
  must still see the story). There's a test that asserts the input pool is unchanged after assembly.
- The mute is stored per-category (`user_mute_terms`, migration 0029, owner-RLS) for provenance,
  but the filter is **global per user** ("gone means gone", PRD #11) — the batch loader
  (`daily_batch._load_mute_terms`) collects terms only, not category.

**3. Clean-replace for subtractive per-user data = delete-all-then-insert.**
Interests use insert-then-delete-stale to avoid a zero-rows window (losing content is dangerous).
Mutes are SUBTRACTIVE — an empty-mute window only lets MORE stories through, never loses user
content — so the simplest correct rebuild-my-feed clean-replace (`persistMuteTerms`,
`src/lib/interviewProfile.ts`) is delete-all-then-insert, which guarantees zero orphaned mutes.
An empty replace set is VALID (user cleared their mutes), unlike the interests refuse-empty guard.

**Stateless-turn schema growth:** new terminal fields (`mute_terms`, `angle_preferences`) are
additive/optional on the payload so old clients ignore them and the one `POST /api/interview/turn`
contract is unchanged. Extending the phase machine means new terminal-expecting TESTS must now also
answer the newly-inserted turns before reaching `terminal` (the whole existing suite had to append
ANGLE+SKIP exchanges).

Gotcha (tooling): typing a template-literal ``${a} ${b}`` via the Edit tool left a literal NUL byte
where the space was, making later exact-match Edits silently fail. If an Edit "can't find" text that
`Read` clearly shows, scan for NULs: `tr -cd '\000' < file | wc -c`.
