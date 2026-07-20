# Slice #62 — read-side headline gate + backfill: residual review findings

Panel: data-integrity lens, correctness lens, simplicity/reuse lens. Everything below was
evidenced but **not** fixed in the slice commit; each entry says why.

Fixed in-slice (recorded so a later reader does not re-file them): the missing
source-origin exemption, the unpaged/unchunked `story_sources` read, the story-scoped
(rather than digest-scoped) update, the discarded write result, the short-page pagination
exit, the duplicated hint-construction rule, and a tautological idempotency test.

## Deferred — needs a human decision

- **`digest_is_current` is the produce-once marker, so retiring re-opens paid production.**
  `agents/pipeline/produce_gate.py` reads exactly the flag this script clears, so a retired
  story is "never produced" again: if it is re-ingested it pays scripting + verification
  (+ editorial rewrite) before the write-time gate drops it at `orchestrator.py:557`. Fine
  for a one-time cleanup of one row; wrong as a recurring suppression mechanism. The
  correct fix is an explicit story-level suppression column the produce gate also reads —
  a migration plus a produce-gate contract change, out of this slice's scope. Documented
  under KNOWN LIMITS in the script so nobody schedules it.

- **The apply path has not been run.** The dry run reports 1 row
  (`cand-6bafb59c2322`, digest `4fa88659-97a4-499f-89b2-d931cfd4e7bc`,
  `title_equals_outlet` — the 07-07 "Language Magazine" story). Marking prod rows
  not-placeable is the founder's call to trigger.

- **`agents/shared/headline_quality.py::_domain_keys` does not strip a leading article,
  but `_comparison_key` does.** So "The Hindu Business Line" on `thehindubusinessline.com`
  is NOT recognised as a masthead (title key `hindubusinessline` vs domain key
  `thehindubusinessline`), while "Modern Farmer Magazine" on `modernfarmermagazine.com`
  is. The one-line symmetry fix would tighten the gate at three WRITE seams that #45 owns
  (`dedup.py`, `editorial.py`, `persist.py`), and this gate has no fallback — a false
  positive drops a whole reel. Not changed unilaterally mid-slice. No prod row currently
  hits this case.

## Deferred — no concrete fix inside this slice

- **The script does not touch `daily_feeds`.** A masthead reel already assembled into a
  past feed keeps rendering: `src/lib/feed/supabaseFeed.ts` joins `digests` without
  filtering on `digest_is_current`. Cleaning the already-shipped 07-07 reel needs a feed
  rebuild or a slot swap on top of retirement. Stated in the script's KNOWN LIMITS.

- **`src/lib/feed/supabaseFeed.ts::mapStoryRow` falls back to `digests[0]` when no digest
  is current** (same at `src/lib/archive/listBriefings.ts`). For a story with more than one
  digest that fallback is an arbitrary, possibly older digest — stale audio/captions and a
  possibly-null poster. Pre-existing app-side behaviour, unrelated to the loader this slice
  changed; retiring a digest makes it reachable, so worth a slice of its own
  ("prefer the newest digest, not `digests[0]`").

- **`scripts/retire_unpublishable_headlines.py::_fetch_all_story_rows` duplicates
  `agents/pipeline/coverage_census.py::_fetch_all`.** The new copy is the more correct of
  the two (it stops on an empty page rather than a short one, so a project `max-rows` below
  the page size cannot truncate the scan). Consolidating means fixing and promoting the
  census helper — a change to an unrelated module's behaviour, so left for a refactor slice.
