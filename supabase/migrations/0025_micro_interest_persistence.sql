-- Migration 0025 — Micro-interest persistence: per-user display label + mint RPC
--
-- Phase FSR (interview onboarding revamp), slice #2 (micro-interest persistence).
-- Source of truth: reference/interview-onboarding-spec.md §4 (terminal schema) + §5
-- (persistence mapping). Consumed downstream by slice #6 (allocator) and #7 (assembly).
--
-- ── WHAT THIS ADDS ──────────────────────────────────────────────────────────────
--  1. user_interest_profile.profile_display_label — the user's OWN vocabulary for a
--     picked interest (spec §5: "per-user display label lands WITH the profile row,
--     NOT the node"). Two users who converge on one shared `interests` node keep their
--     own on-screen label here. Nullable: legacy rows + roots-only picks may have none.
--  2. mint_interest_ladder(canonical_slug, search_query) — a SECURITY DEFINER RPC that
--     mints the FULL root→leaf node chain for a terminal micro-interest and returns the
--     leaf interest_id. The interview persister (src/lib/interviewProfile.ts) runs in
--     the BROWSER under the authenticated anon-key JWT; `interests` is public-read with
--     NO write policy (migration 0003), so the client physically cannot INSERT taxonomy
--     nodes (the content_sources RLS write-gap incident, 2026-06-17, is the same class
--     of bug). Rather than open a blanket authed-INSERT policy on the global taxonomy
--     (no validation, any authed user could write any row), this definer RPC is the ONE
--     narrow, validated write seam — mirroring the SECURITY DEFINER pattern of
--     handle_new_user (0003) and user_personality_spotlights (0009).
--
-- ── ADDITIVE / expand-only · forward-safe deploy window (migration lens) ─────────
--  * `add column if not exists` is additive: OLD code (which never selects or writes
--    profile_display_label) keeps working — the column is nullable with no default, so
--    an old-code INSERT that omits it simply leaves it NULL. NEW code writes it. No
--    contract break either direction during the rolling deploy → expand/contract safe.
--  * The RPC is brand-new: no existing caller references it, so adding it cannot break
--    old code. `create or replace function` makes re-apply a pure no-op.
--  * No DROP, no destructive ALTER, no data rewrite. Reversible by dropping the column
--    + function (see ROLLBACK below).
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * Column add is `if not exists`; function is `create or replace`; grants are
--    declarative. Re-applying the whole file is a no-op.
--  * mint_interest_ladder itself is idempotent at CALL time: every node is an
--    `insert … on conflict (interest_slug) do update` (upsert by slug), so re-minting
--    the same slug returns the same interest_id and never duplicates a node — this is
--    exactly the CROSS-USER convergence key (spec §4: two users who type the same niche
--    differently converge on ONE node).
--
-- DEPENDS ON: 0003 (interests + user_interest_profile + the ck_interest_depth check +
-- interest_slug unique), 0020 (the segment_slug enum values the `::segment_slug` cast in
-- the RPC needs), 0021 (the 8 `segments` rows the interest_segment_slug FK references), and
-- 0023 (the 8 depth-0 root interest nodes — the mint targets). On a fresh env, minting under
-- ai/geopolitics/environment/politics/arts fails without 0020+0021+0023 present.
-- Apply order: 0020 → 0021 → 0023 → (0024) → 0025.

-- ── 1. Per-user display label on the profile row ─────────────────────────────────
alter table user_interest_profile
  add column if not exists profile_display_label text;

comment on column user_interest_profile.profile_display_label is
  'The user''s own vocabulary for this interest (drives feed section headers). Lives '
  'with the profile row — NOT the shared interests node — so two users converging on '
  'one node keep distinct labels. NULL for legacy / roots-only picks.';

-- ── 2. mint_interest_ladder — validated, definer-privileged node minting ──────────
-- Mints the full root→leaf chain for one terminal micro-interest and returns the LEAF
-- interest_id. Runs root→leaf so every child's parent already exists (satisfies the FK
-- and ck_interest_depth: depth 0 ⇒ no parent, depth > 0 ⇒ parent). Called once per
-- accepted micro-interest by src/lib/interviewProfile.ts.
--
-- The ENTIRE chain is minted inside ONE function invocation, which runs in a single
-- implicit transaction: if any node fails (or a `raise` fires), the whole call rolls
-- back — there is no half-minted ladder (data-integrity: no orphaned intermediate node).
--
-- Validation (defense-in-depth backstop — the browser persister validates first, but a
-- definer-privileged write must never trust its caller):
--   * non-empty slug;
--   * ≤ 4 dotted segments (drill-down depth ≤ 3, spec §3);
--   * every segment matches ^[a-z0-9]+(-[a-z0-9]+)*$ (mirrors guards._SLUG_SEGMENT_RE);
--   * the ROOT segment is already a depth-0 interest (root-anchoring — the caller cannot
--     invent a new root, only mint niches UNDER an existing depth-0 root). The later
--     `::segment_slug` cast further pins minted nodes to a root that is a valid segment
--     (the 8 canonical roots), so a legacy depth-0 alias that is not a segment fails loudly.
--     The browser persister is the CANONICAL-root gate (its TOPIC_ROOT_SLUGS = the 8);
--     this RPC is the untrusted-caller backstop.
-- A root-only slug (single segment, the skip-everything / roots-only path) mints NOTHING
-- and returns the existing root id — the loop range `2 .. 1` is empty in plpgsql.
create or replace function public.mint_interest_ladder(
  p_canonical_slug text,
  p_search_query   text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_segments  text[];
  v_seg_count integer;
  v_root_slug text;
  v_slug      text;
  v_parent_id uuid;
  v_node_id   uuid;
  v_idx       integer;
  c_seg_re    constant text := '^[a-z0-9]+(-[a-z0-9]+)*$';
begin
  if p_canonical_slug is null or p_canonical_slug = '' then
    raise exception 'mint_interest_ladder: empty canonical_slug' using errcode = '22023';
  end if;

  v_segments  := string_to_array(p_canonical_slug, '.');
  v_seg_count := array_length(v_segments, 1);
  v_root_slug := v_segments[1];

  -- Drill-down depth cap (spec §3): ≤ 3 levels below a root ⇒ ≤ 4 segments incl. root.
  if v_seg_count > 4 then
    raise exception 'mint_interest_ladder: slug too deep (% segments): %',
      v_seg_count, p_canonical_slug using errcode = '22023';
  end if;

  -- Every segment must be a well-formed slug token (no injection, no fabricated punctuation).
  for v_idx in 1 .. v_seg_count loop
    if v_segments[v_idx] !~ c_seg_re then
      raise exception 'mint_interest_ladder: malformed slug segment "%" in %',
        v_segments[v_idx], p_canonical_slug using errcode = '22023';
    end if;
  end loop;

  -- Root-anchoring: the root segment MUST already be a depth-0 interest (0023). This is
  -- what stops a caller minting a node under a root that does not exist.
  select interest_id into v_node_id
  from interests
  where interest_slug = v_root_slug and depth_level = 0;
  if v_node_id is null then
    raise exception 'mint_interest_ladder: root "%" is not a depth-0 interest', v_root_slug
      using errcode = '23503';
  end if;

  -- Walk root → leaf, minting each missing node. Upsert by slug = cross-user convergence:
  -- a second user whose niche resolves to the same slug reuses the existing node (and only
  -- back-fills a missing interest_search_query — an existing curated label/query is kept).
  v_slug := v_root_slug;
  for v_idx in 2 .. v_seg_count loop
    v_parent_id := v_node_id;
    v_slug := v_slug || '.' || v_segments[v_idx];
    insert into interests (
      interest_slug,
      interest_label,
      depth_level,
      parent_interest_id,
      interest_segment_slug,
      interest_search_query,
      interest_kind
    ) values (
      v_slug,
      initcap(replace(v_segments[v_idx], '-', ' ')),
      v_idx - 1,
      v_parent_id,
      v_root_slug::segment_slug,
      case when v_idx = v_seg_count then nullif(p_search_query, '') else null end,
      'taxonomy'
    )
    on conflict (interest_slug) do update
      set interest_search_query =
            coalesce(interests.interest_search_query, excluded.interest_search_query)
    returning interest_id into v_node_id;
  end loop;

  return v_node_id;
end;
$$;

-- SECURITY DEFINER hardening: revoke the implicit PUBLIC execute grant AND the `anon`
-- grant (Supabase's ALTER DEFAULT PRIVILEGES auto-grants EXECUTE on new public functions
-- to anon/authenticated/service_role, and `revoke … from public` does NOT touch the named
-- `anon` role), then re-grant only to authenticated + service_role. Minting taxonomy is an
-- authed-only action, so an UNAUTHENTICATED (anon) caller must not invoke this definer mint.
revoke all on function public.mint_interest_ladder(text, text) from public;
revoke all on function public.mint_interest_ladder(text, text) from anon;
grant execute on function public.mint_interest_ladder(text, text) to authenticated, service_role;

-- Verification (run after apply — see the acceptance-criteria check):
--   -- the column exists and is nullable:
--   select column_name, is_nullable from information_schema.columns
--   where table_name = 'user_interest_profile' and column_name = 'profile_display_label';
--   -- the RPC exists and is SECURITY DEFINER:
--   select proname, prosecdef from pg_proc where proname = 'mint_interest_ladder';
--   -- a smoke mint (rolled back) mints the ladder + returns the leaf id:
--   -- begin; select mint_interest_ladder('sport.cricket.ipl.auctions','ipl auctions, ipl'); rollback;
--
-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ───────────────────────
--   drop function if exists public.mint_interest_ladder(text, text);
--   alter table user_interest_profile drop column if exists profile_display_label;
-- (Dropping the column discards per-user labels but never breaks a FK — profile rows and
-- the interests taxonomy are untouched. Nodes minted by the RPC are ordinary interests
-- rows and remain valid after the function is dropped.)
