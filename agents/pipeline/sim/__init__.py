"""Offline ranking simulation (dev tooling, zero paid APIs).

A deterministic synthetic "world" (~100 stories + a taxonomy + user profiles) run
through the REAL ranking + allocation code (``agents.pipeline.stages.ranking`` +
``agents.pipeline.feed_assembly``) so we can SEE what surfaces at the top of each
profile's feed and WHY — without GDELT, TTS, LLM, or Supabase.

Validates the thing the 2-user fixture e2e cannot: interest-ranking at scale.
See ``reference/ranking-spec.md`` for the formula this exercises.
"""
