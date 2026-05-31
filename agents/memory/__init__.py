"""Profile-update loop (Phase 1d SP4).

The daily engagement → interest-weight job (ranking-spec §4): aggregate
``player_signals`` since the last run and nudge ``user_interest_profile.profile_weight``
with bounded, slow-decay updates so the feed feels personal over time without
over-narrowing.

Structurally modeled on the TLDW donor's ``agents/memory`` signal→weight loop, but
rewritten for News20's reel engagement signals + ``reference/ranking-spec.md`` §4
(different schema; see the SP4 report's divergence note).
"""
