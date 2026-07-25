---
title: A side-band signal smuggled into a shared channel corrupts every reader of that channel
tags: [channel-separation, story-interests, theme-category, assign-category, shortlist, phantom-tags, depth-semantics]
problem_type: pattern
symptoms: scrambled category chips (cricket→tech, Netflix→geopolitics); matched-interest lists naming roots nobody follows; a field documented as "leaf-matched (depth 0)" returning only segment roots; merge pins freezing wrong verdicts batch-wide
root_cause: a category signal (theme-derived) was encoded INTO the interest-match channel as a depth-0 ROOT story_interests tag, with real keyword tags shifted to depth >= 1 — so every reader that assumed "a tag = a verified interest match, depth 0 = the fetching leaf" silently lied
date: 2026-07-25
---

## What happened (issue #70, commit c38092f)

FSR-M2 SP3 wanted "category from what the story is ABOUT (GDELT themes), not which
query fetched it". Instead of adding a new channel, it encoded the theme category as
a **depth-0 tag on the category's ROOT interest node** inside `story_interests`, and
shifted all keyword tags +1 so the theme tag always won `assign_category`'s
lowest-depth rule. Every downstream reader of the tag stream then lied at once:

- `build_produce_shortlist` read "depth 0 = leaf-matched interests whose queries
  surfaced this story" → returned **theme roots the founder does not follow**
  (phantom `environment`/`politics` slugs), never one of his 26 real leaves.
- `assign_category` let the noisy theme whitelist (health→tech pin, ARMEDCONFLICT,
  disaster codes) **override the two-key-verified fetching interest**: a cricket
  injury story chipped `tech` via MEDICAL/GENERAL_HEALTH codes.
- Reconcile's merge pin froze the representative's (theme-scrambled) provisional
  category onto whole merges; tag unions mixed several members' theme ROOT tags at
  depth 0, producing 4-root phantom matched lists.
- Ranking's fallback climb saw theme root tags as strong phantom ancestor matches;
  the produce gate's `serves_interest_count` counted them as served interests.

The 2026-07-25 audit failed 21/55 shortlist rows on exactly these shapes.

## Rules

1. **One channel, one meaning.** If a field's readers assume "row = verified
   interest match, depth = climb distance", never encode a second signal into that
   field by manipulating the discriminator (here: reserving depth 0 and shifting
   everything else). The moment two signals share a channel, every existing reader
   silently misreads BOTH.
2. **Carry the second signal as an explicit side-channel.** The fix:
   `theme_categories_for_stories(stories) -> {story_id: category}` built once per
   batch and passed to `assign_category(theme_category_by_story=...)`, where it may
   only (a) break an equal-lowest-depth category tie among verified contenders or
   (b) categorize a story with zero resolvable tags. Aboutness tiebreaks; it never
   overrides a verified match.
3. **When two doctrines collide, re-check which premise expired.** M2's
   "theme beats keyword" existed because keyword fetches had lexical false
   positives. The two-key relevance lock (#50 lexical + #51 semantic) later fixed
   that at the matched-id source — which silently expired M2's premise. Rule 7:
   the newer doctrine (verified keyword wins) replaced the older one, explicitly,
   with the old tests rewritten to encode the new WHY.
4. **A merge pin must be the union resolution, not the representative's opinion.**
   Reconcile now pins a conflicted merge to `assign_category` over the MERGED tags
   + MERGED themes — the same verdict any downstream call site would compute — so
   a rep chosen for headline quality/recency can no longer export its own
   mis-categorization to the whole cluster.
5. **Guard the artifact with a followed-set invariant that fails loud.** The
   shortlist now warns per row (`shortlist_matched_slug_outside_followed_set`,
   with fix_suggestion) whenever a matched slug falls outside the batch's followed
   universe — the corruption shape is detectable the day it regresses, not at the
   next founder audit.

## How to spot it early

A field whose docstring says "X (i.e. Y)" where Y is a mechanism ("leaf-matched
(depth-0)") is a tripwire: when the mechanism's semantics change upstream, the
docstring keeps promising X while the code delivers whatever Y now means. Grep for
readers of the discriminator (`match_depth == 0`) whenever tag emission changes.
