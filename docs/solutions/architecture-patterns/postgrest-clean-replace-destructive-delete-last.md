---
title: Clean-replace of owner rows over PostgREST (no transaction) — upsert first, destructive delete LAST, refuse empty, tag partial failures
tags: [postgrest, supabase, replace-semantics, data-integrity, rls, rebuild-my-feed]
problem_type: architecture
symptoms: A "replace the user's rows with this new set" flow over supabase-js has no transaction — naive delete-then-insert leaves zero rows on failure, insert-then-delete can blend old and new, an empty new set silently wipes everything, and the error UI lies about what state the data is in
root_cause: PostgREST exposes single-statement writes only; multi-step replace semantics must be ordered and classified client-side (or moved into a SECURITY DEFINER RPC for true atomicity)
date: 2026-07-03
---

Established in slice #9 (rebuild-my-feed replaces `user_interest_profile`). Reuse for any
client-side replace of a user-scoped row set. The review panel caught all three defects
below in the naive version — treat them as a checklist, not hypotheticals.

**The ordering rule: additive writes first, the destructive delete LAST.**
`upsert(newRows)` → auxiliary upserts (traits) → `delete().eq(owner_col, userId).not(key_col, "in", "(kept1,kept2)")`.
Every failure point then leaves either (a) the old rows fully intact, or (b) a retryable
old∪new *superset* — never zero rows, never a torn set that loses data. The retry is
idempotent end-to-end (upsert converges, delete re-sweeps).

**Refuse an empty accepted set in replace mode.** An empty kept-list means the delete has
no `not.in` filter → it wipes EVERY row for the user. Two paths reach it: a legitimate
skip-through (caller should treat as "keep old", not persist) and a validation backstop
rejecting the whole payload (a bug upstream — wiping over it strands the user). Throw
before any write; handle "user confirmed empty" at the flow layer as a no-op.

**Tag post-upsert failures so the error UI can tell the truth.** A failure BEFORE the
upsert = "your data is untouched" (bail-out is safe). A failure AFTER = "new rows saved,
old cleanup pending — retry to finish" (bail-out copy must NOT claim untouched). Tag with
`error.name = "ReplacePartialError"` (a shared exported const) rather than message
sniffing; the flow branches copy + bail-affordance on it.

**Known limit (deferred, not solved):** two concurrent replaces can interleave to the
intersection of both sets. True atomicity needs a SECURITY DEFINER RPC doing the
replace in one transaction (same seam pattern as `mint_interest_ladder`, migration 0025).

**Test seam:** a thenable fake supabase filter-builder (`then(resolve)` + biome-ignore
`noThenProperty`) lets vitest capture `delete().eq().not()` chains and assert a
`writeOrder` log proves the delete is last — see `tests/lib/interviewProfile.test.ts`.
