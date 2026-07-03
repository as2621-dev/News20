# Residual review findings — slice #9 (rebuild-my-feed entry + re-interview)

Multi-agent review panel (correctness, simplicity/reuse, data-integrity,
contract/architecture) on the slice diff. All HIGH findings were **fixed before
commit**: the empty-confirm wipe-all (replace now refuses an empty accepted set and the
flow treats an empty confirm as keep-old), the post-upsert partial-failure blend with
false "untouched" copy (destructive delete moved LAST, post-upsert failures throw
`REPLACE_PARTIAL_ERROR_NAME`, error copy is state-aware), and the silently-logged
rejected picks (now surfaced on the done screen). The items below are advisory or need
a human design call and were deliberately deferred.

## Deferred (human call / separate slice)

- **Replace atomicity under concurrency.** The replace is client-side
  upsert→traits→delete; two concurrent rebuilds (two tabs, or confirm raced with a
  network retry at the transport layer) can interleave as upsertX, upsertY,
  delete-not-X, delete-not-Y → final profile X∩Y. The in-component `isPersistingRef`
  guards only one mount. The clean fix is a single SECURITY DEFINER
  `replace_interest_profile(p_rows jsonb)` RPC (transactional, same seam pattern as
  `mint_interest_ladder`) — that is a new migration, so a human/product call. Risk is
  low (a user racing themselves across tabs), and any bad interleaving is repaired by
  simply re-running the rebuild.

- **Partial backstop rejection shrinks the replaced profile.** In replace mode a
  rejected pick's OLD row is swept with the other stale rows, so the profile equals the
  accepted subset. The done screen now says "We couldn't keep N of your picks", but
  whether a high rejection ratio should abort the replace instead is a product call.
  (Rejections only occur on a worker/client contract bug — the worker validates the
  same rules first.)

## Advisory (low)

- **`not.in.(...)` rides the DELETE URL.** A very large confirmed set could hit URL
  length limits (fails loud — old rows kept, retry offered). Not reachable today: the
  interview engine steers to terminal at ~15–18 taps, so sets stay ≪ 100.

- **e2e harness duplication.** `tests/e2e/rebuildFeed.e2e.mjs` repeats the session-seed
  + CORS-stub + storyEmbed scaffolding of `reelSections.e2e.mjs` / `interviewChat.e2e.mjs`
  (per-file convention, Rule 11). A shared `tests/e2e/harness.mjs` extraction is its own
  refactor slice — it touches prior slices' committed files.

## Flagged (green tests, worth eyes)

- The slice changes a **public contract** (`PersistInterviewOptions.replace_existing`,
  `REPLACE_PARTIAL_ERROR_NAME`, `InterviewChatProps.forceRestart`) and performs
  **destructive writes** (user-scoped profile-row delete, RLS owner-all verified in
  migration 0003). Defaults preserve prior behavior exactly; OnboardingFlow is untouched.
