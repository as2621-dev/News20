---
title: "Names/handles-only" for sources is a RENDER-layer invariant — prod content_sources DOES carry seeded thumbnail_urls, so tiles must degrade on null AND onError
tags: [sources, thumbnail_url, avatar, seed, yt-dlp, render, frontend, founder-decision, operational]
problem_type: operational-gotcha
symptoms: an issue/plan assumes "nothing populates thumbnail_url" and closes a source
  render as already-satisfied; a followed YouTube source shows a broken/empty avatar
  tile with no initial; a raw remote <img> for a source avatar has no onError fallback
root_cause: the v2 catalog seeder (scripts/seed_catalog/seed_v2.py) writes
  thumbnail_url = meta.thumbnail_url (a keyless yt-dlp CDN URL, e.g. i.ytimg.com) into
  content_sources for the ~152 seeded YouTube channels, so thumbnail_url is NON-NULL in
  prod for those rows — the "nothing populates thumbnail_url" premise is false. A raw
  <img src={thumbnail_url}> without onError leaves a broken/empty tile when that CDN URL
  404s or rejects the hotlink.
date: 2026-07-05
---

Context: the founder's 2026-07-05 "no avatars — names/handles only, no YouTube Data API"
decision (FSR #21). The tempting read is "since nothing populates thumbnail_url, the
initials fallback already makes this names/handles-only — already satisfied." That read
is wrong at the data layer and right only if the RENDER is resilient.

**Facts to carry forward:**

- `scripts/seed_catalog/seed_v2.py:297` writes `thumbnail_url` from
  `youtube_resolve` (`_pick_thumbnail(info["thumbnails"])`, keyless yt-dlp). So prod
  `content_sources.thumbnail_url` is NON-NULL for seeded YouTube channels. Do not assume
  it is always null.
- "Names/handles only" is therefore enforced at the **render layer**, not the data
  layer. The invariant a source avatar tile must hold: resolve to a legible name-derived
  initial when `thumbnail_url` is null **OR** when the `<img>` fires `onError`
  (single-shot state flag — flip to the fallback, which removes the `<img>` from the
  tree so the error can't loop). Mirror `src/components/sources/SourceArtwork.tsx`.
- Any remote source-avatar `<img>` also needs `referrerPolicy="no-referrer"` — the
  external CDNs (i.ytimg.com, pbs.twimg.com, mzstatic.com) are not whitelisted in
  next.config and reject hotlinks without it. `SourceArtwork`/`SourceSwipeCard` already
  do this; a new tile that omits it degrades to the initial far more often than needed.
- The shared library-tile fallback lives in
  `src/components/blip/library/SourceAvatarImage.tsx` (used by `SourcesScreen` followed
  list + `SourcesAddControls` search results). Reuse it rather than re-inlining a raw
  `<img>` at a third library follow-row.

**Scope boundary:** "no avatar fetch / no YouTube Data API" (the killed original scope
= a one-time fetch into a `source-avatars` bucket) is about not building a dedicated
avatar-fetch job/bucket — grep-prove `source-avatars` is absent. It does NOT mean
ripping out the worker's source-**search** (agents/worker/main.py, which uses the YT
Data API to resolve channels to add) or the keyless yt-dlp catalog seed — those are
pre-existing catalog/search flows, out of scope for a frontend render-hygiene slice.

**Open founder call:** if pure names/handles-only (no avatar even when it loads) is
wanted, it is a one-line change in `SourceAvatarImage` (ignore `thumbnail_url`, always
render the initial). As of #21 the seeded avatars still display as a progressive nicety.
