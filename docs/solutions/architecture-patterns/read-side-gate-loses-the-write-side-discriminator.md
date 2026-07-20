# Re-applying a write-time gate on a READ path: the discriminator is usually gone

**Date:** 2026-07-18 · **Slice:** #62 (commit 5ae3493) · **Files:**
`agents/shared/persisted_headline_gate.py`, `agents/worker/pipeline_routes.py`,
`agents/ingestion/dedup.py`, `scripts/retire_unpublishable_headlines.py`

## Problem

Slice #45 made the headline gate fail-closed at WRITE time. Rows persisted before it
existed still shipped, so #62 re-applied the same predicate on the one read path that
resurrects a persisted row into a feed (`_load_ready_story_pool`). Calling the shared
predicate looked like the whole job. It was not: the write-time gate exempts
followed-source (YouTube/X) reels via `is_source_origin_domain(story.canonical_primary_outlet_domain)`,
and **that field does not survive persistence**. `stories.story_primary_outlet_name`
holds the *channel name* for a source reel, never `youtube.com`. A naive re-application
would have dropped every subscribed creator's short-titled upload ("Ferrari") out of the
pool — the gate's exemption silently inverted into the exact data loss it was written to
prevent.

## Rules

1. **Before re-applying a gate on a read path, diff the gate's INPUTS, not just its
   predicate.** Reusing the predicate is necessary and not sufficient. List every field
   the write-time gate branches on (`canonical_primary_outlet_domain` here) and check each
   one actually exists on the persisted row. A field the write path took for granted is
   the most likely thing the read path silently defaults, and a defaulted exemption fails
   OPEN or CLOSED depending on which way the branch runs — both are wrong.
2. **When the discriminator is gone, reconstruct it from the row that DID keep it.**
   The origin survived on `story_sources.source_article_url`, so
   `source_origin_story_ids_from_source_rows` recovers it. Cheaper and more honest than
   adding a column, and it keeps one definition of "source-origin" (`SOURCE_ORIGIN_DOMAINS`).
3. **A read-path gate and its cleanup script must share ONE row-shaped verdict function.**
   `persisted_headline_rejection_reason(story_row, source_origin_ids)` is called by both.
   Two copies of "offer the outlet column as both name and domain" had already been
   written, each with a comment warning they must agree — that comment is the tell that
   the code wants to be one function. A story the script retires but the loader would
   place is a lost reel; the reverse is a permanent silent skip.
4. **`.in_()` over a child table needs chunking AND paging, not one or the other.**
   `story_sources` holds one row per covering outlet and only the primary row carries the
   URL, so an unpaged read truncates at PostgREST's 1000-row cap and drops exactly the
   sparse rows the exemption depends on — nondeterministically, since there was no
   `.order()`. Chunk the ids at 150 (URL-length) *and* page each chunk (row cap).
   Terminate on an EMPTY page, not a short one: the project's `max-rows` may be below
   the page size, and a short-page exit then reads only page one.
5. **A cleanup script must target the row it means, not the row's owner.** Retiring by
   `digest_id` (captured in the scan, printed as an undo manifest) rather than by
   `story_id` is what stops the write from clobbering a newer digest produced between the
   scan and the confirmation prompt. Return and check the updated row count — a discarded
   `.execute()` response turns a partial prod mutation into a printed success (Rule 12).
6. **Check what the flag you are flipping ALSO means.** `digest_is_current` is both
   "placeable" and the produce-once marker (`agents/pipeline/produce_gate.py`). Clearing
   it to suppress a story also tells the pipeline the story was never produced, so a
   re-ingest pays for scripting + verification again. Acceptable for a one-time cleanup of
   one row; wrong as a recurring mechanism. Say so in the script instead of discovering it
   on the third scheduled run.

## Applies with

`architecture-patterns/drop-gate-needs-sibling-selection-parity.md` — that entry is about
sibling *selection* rules feeding the gate the wrong candidate at write time. This one is
the read-time twin: the same gate, re-applied where its inputs no longer exist.
