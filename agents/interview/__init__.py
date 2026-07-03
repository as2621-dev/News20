"""Conversational onboarding interview engine (FSR interview slice #1).

Drives the chat-interview that replaces the static picker: one turn per request,
server-driven, stateless. Turn 1 returns the 8 fixed roots (no LLM); deeper turns
call Gemini Flash for structured next-question / terminal-extraction decisions,
with all hard guardrails (drill-depth cap, tap budget, terminal validation,
dedup) enforced in code. See ``reference/interview-onboarding-spec.md``.
"""
