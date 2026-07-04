"""Conversational onboarding interview engine (FSR interview slices #1 + #14).

Drives the chat-interview that replaces the static picker: one turn per request,
server-driven, stateless. Turn 1 returns the 8 fixed roots (no LLM). The INTERESTS
phase then runs a deterministic, table-driven state machine (:mod:`.phases`): a
multi-select sub-niche turn per lit root, exactly one open WHO drill per selected
sub-niche (compressed past a cap), and an engine-owned skip fast-forward
(category-skip → build-my-feed offers). Only the sub-niche turns and the terminal
extraction call Gemini Flash; every offer and WHO drill is engine-worded. All hard
guardrails (flow control, terminal validation, dedup, deferred-skip records) are
enforced in code (Rule 5). See ``reference/interview-onboarding-spec.md``.
"""
