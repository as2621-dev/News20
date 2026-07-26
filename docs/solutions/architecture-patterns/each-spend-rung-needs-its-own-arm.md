---
title: Opting out of one halt is not permission for the next stage — every spend rung needs its own arm
tags: [spend-safety, env-flags, staged-approval, halt-ladder, entry-points, defaults]
problem_type: architecture-pattern
symptoms: a review gate exists and works, but approving it releases everything downstream
  in one shot; the reviewer can only approve the CHEAP artifact (a list of headlines) and
  is billed for the expensive one (recorded media) before ever seeing it; there is no
  spelling of the flags that says "yes to the next step, not the whole run"
root_cause: the halt was modelled as a BOOLEAN (halt / don't halt) over a pipeline whose
  cheap and expensive stages were fused in one function, so "not halted" necessarily meant
  "run everything"
date: 2026-07-25
---

Found in issue #68. `SHORTLIST_ONLY=1` halted the daily batch above script writing, and
production fused script → TTS → poster into one stage. The founder could approve a list
of headlines, and that single `SHORTLIST_ONLY=0` bought scripts *and* 58 reels' TTS +
posters (the 2026-07-19 $24 burn). The gate was real; it just guarded the wrong seam.

**A halt boolean over a fused stage can only ever ask "spend nothing or spend
everything".** The fix is two-part and neither half works alone:

1. **Split the fused stage at the price boundary.** Here: `_write_script_pool` (text on
   `gemini-3.5-flash`, pennies) and `_render_story_pool` (TTS + posters + persist). The
   split is what makes an intermediate halt *expressible*.
2. **Give the expensive stage its own OPT-IN arm** (`PRODUCE_REELS`, unset = off), rather
   than letting the cheap stage's opt-out imply it. Opting out of rung N must advance the
   run exactly one rung.

**The load-bearing test is the middle rung, not the ends.** `nothing set → halt` and
`everything set → produce` both pass even when the ladder collapses. The assertion that
catches the collapse is `SHORTLIST_ONLY=0 alone → scripts, NOT reels`. Same shape as the
older lesson in `safety-default-read-inline-is-not-a-default.md`: test the default and
the *gap between* the flags, never the mapping.

**Parse the two flag kinds as mirrors.** Opt-out (safety on by default) turns off only on
`0`/`false`/`no`/`off`; opt-in (spend off by default) turns on only on `1`/`true`/`yes`/
`on`. Anything unrecognized keeps the safe value in both directions, so a typo can neither
disable a guard nor authorize money.

**Make the halt structural, not merely conditional.** After the split, the expensive
clients (`tts_client`, `poster_genai_client`) are referenced in exactly ONE place in the
runner — the render call *below* the halt — so "can media fire under the halt?" is
answerable by grep. Back that with a spy that raises on ANY attribute access rather than a
mock that records calls: it catches the client being *reached*, not just used. Deleting
the halt then fails the test with a real `call_gemini_multispeaker_tts` in the log.

**Keep the fused function as a thin composition of the two halves.** Its existing tests
are the only cheap proof that the armed path is byte-for-byte unchanged; retargeting them
at the new halves in the same commit would have thrown that proof away.

**A per-run arm needs a precedence rule, or a stale value becomes a standing
authorization.** `SHORTLIST_ONLY` outranks the arm, so `PRODUCE_REELS=1` forgotten in a
Railway dashboard cannot resurrect spend the moment someone re-enables the earlier halt.
