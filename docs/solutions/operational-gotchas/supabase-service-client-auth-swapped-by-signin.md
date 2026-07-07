---
title: signInWithPassword on a service-role Supabase client silently demotes it to the user
tags: [supabase, service-role, rls, signInWithPassword, e2e]
problem_type: tooling
symptoms: service-role mutation returns `{ data: [], error: null }` on rows that exist; orphan test rows accumulate
root_cause: auth.signInWithPassword sets the client's in-memory session (even with persistSession false) so later calls send the user token, not the service-role key
---

# signInWithPassword on a service-role Supabase client silently demotes it to the user

**Date:** 2026-07-07 · **Surfaced by:** issue #33 browser walkthrough (seeded feed-row cleanup)

## Problem

A script created ONE client with `SUPABASE_SERVICE_ROLE_KEY`, used it to seed rows
(worked — service role bypasses RLS), then called `client.auth.signInWithPassword(...)`
to mint a user session for localStorage injection. Every query on that client AFTER
the sign-in ran as the signed-in USER: the cleanup `delete()` matched 0 rows with
`error: null` (RLS filtered them out — no error, just silence), leaving orphan rows
in prod `daily_feeds` across runs (surfacing later as duplicate-key insert failures).

## Root cause

`signInWithPassword` sets the client's in-memory session even with
`persistSession: false`; subsequent PostgREST calls send the user's access token as
the Authorization header instead of the service-role key.

## Fix / rule

Use SEPARATE clients: an anon-key client for `signInWithPassword` (session minting)
and a service-role client that NEVER calls any `auth.sign*` method. Symptom to watch
for: service-role mutation returns `{ data: [], error: null }` on rows you know exist.
