---
title: SECURITY DEFINER RPCs — `revoke from public` does NOT lock out `anon`
tags: [supabase, security-definer, rpc, rls, anon, grants, migration]
problem_type: security
symptoms: a definer RPC meant to be authed-only is still EXECUTE-able by the anon (unauthenticated) role; pg_proc.proacl shows `anon=X/...`
root_cause: Supabase's ALTER DEFAULT PRIVILEGES auto-grants EXECUTE on every new public-schema function to anon/authenticated/service_role; `revoke … from public` removes the PUBLIC pseudo-role grant but NEVER touches the named `anon` role
date: 2026-07-03
---

Building the interview mint RPC (`mint_interest_ladder`, migration 0025) I copied the
established hardening pattern from 0009's `user_personality_spotlights`:

```sql
revoke all on function public.fn(args) from public;
grant execute on function public.fn(args) to authenticated, service_role;
```

The live `pg_proc.proacl` after apply was
`{postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,service_role=X/postgres}` —
**anon still had EXECUTE.** Supabase runs `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT
EXECUTE ON FUNCTIONS TO anon, authenticated, service_role`, so a freshly-created function is
auto-granted to the *named* `anon` role. `revoke … from public` only strips the PUBLIC
pseudo-role — it does nothing to `anon`. For a `SECURITY DEFINER` function this is a real
leak: it runs with owner privileges regardless of caller, so anon could invoke it.

**Fix — also revoke the named `anon` role explicitly:**

```sql
revoke all on function public.fn(args) from public;
revoke all on function public.fn(args) from anon;   -- the line the 0009 pattern is missing
grant execute on function public.fn(args) to authenticated, service_role;
```

Apply this to EVERY authed-only definer RPC. Verify after apply, don't assume:

```sql
select proname, prosecdef, proacl::text from pg_proc where proname = 'fn';
-- proacl must NOT contain `anon=`; must contain `authenticated=X` + `service_role=X`.
```

Note: the pre-existing `user_personality_spotlights` (0009) likely has the same anon leak —
worth an audit sweep of all `security definer` functions if any handle sensitive writes.
Also: `revoke … from public` and `revoke … from anon` are idempotent, so you can apply the
missing revoke as a delta to an already-shipped function without a new migration number.
