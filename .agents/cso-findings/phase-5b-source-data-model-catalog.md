# CSO findings — phase-5b-source-data-model-catalog

**Date:** 2026-06-05
**Scope:** full phase diff (migration 0009, seed, catalog seeder, TS data layer).

## LOW — `youtube_api_key` should be `SecretStr` for convention consistency
- **File:** `agents/shared/settings.py` (the `youtube_api_key: str | None` field added in SP3).
- **Risk:** sibling secrets (`gemini_api_key`, Serper key) use `pydantic.SecretStr`, which prevents accidental `repr()`/log leakage. The new YouTube key is a plain `str | None`, diverging from that convention (Rule 11) and from CLAUDE.md's env-var-safety rule.
- **Mitigation already in place:** the resolver strips the `key` param before logging (`scripts/seed_catalog/youtube_resolve.py` `safe_params`), and the seeder is service-role/server-side only. Actual leak risk is low.
- **Fix (deferred):** change to `SecretStr` and call `.get_secret_value()` at the YouTube HTTP boundary. Ripples through `settings.py` → `seed_catalog.py` → `youtube_resolve.py` + its tests. Not done at the phase gate to avoid churning a green phase; pick up when the seeder is first wired for a live run (needs real keys anyway).

## No critical/high findings.
RLS verified correct (public-read `using(true)` no-write; `user_*` owner-scoped via `auth.uid()`); no new dependencies; no hardcoded secrets; API key excluded from logs.
