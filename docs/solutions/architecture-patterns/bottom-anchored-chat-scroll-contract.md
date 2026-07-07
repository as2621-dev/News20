---
title: Bottom-anchored chat scroll — flex-end breaks scrolling; use first-child margin-top:auto + a pin-only-when-at-bottom hook
tags: [chat-ui, scroll, css, flexbox, autoscroll, jsdom, e2e, ask-sheet]
problem_type: pattern
symptoms: chat thread clips old messages instead of scrolling; new messages yank a scrolled-up reader to the bottom; jsdom tests can't assert scroll behavior (scrollHeight always 0)
root_cause: "justify-content: flex-end on an overflow container makes above-fold content unreachable (no scroll upward), so bottom-anchored chats built that way must choose between anchoring and scrollability; unconditional scrollIntoView on growth ignores the reader's scroll position"
date: 2026-07-07
---

Surfaced building issue #40 (chat UX contract for the typed + voice ask sheets). Reusable for any
bottom-anchored, growing list (chats, logs, transcripts).

**CSS (the anchoring/scrolling trade-off).** `display:flex; flex-direction:column;
justify-content:flex-end` pins content to the bottom but makes overflowing content UNREACHABLE —
the box has nowhere to scroll. The working combination:

```css
.thread { display: flex; flex-direction: column; overflow-y: auto; }
.thread > :first-child { margin-top: auto; }  /* short thread sits at the bottom */
```

Short content bottom-anchors via the auto margin; long content overflows normally and scrolls.
(`::before` as the spacer also works but adds a phantom `gap` slot.)

**Pinning (the yank bug).** Never `scrollIntoView` unconditionally on growth. Track "is the user
at the bottom" in a scroll handler (`scrollHeight - scrollTop - clientHeight <= ~48px`) and only
then set `scrollTop = scrollHeight` when content grows. Shared hook:
`src/lib/chat/useBottomAnchoredScroll.ts` (used by AskSheetType + AskSheetVoice) — pass the
values whose change means "the thread grew" as the effect deps.

**Testing.** jsdom has no layout — `scrollHeight`/`clientHeight` are 0, so scroll tests silently
pass as "pinned". Stub geometry per element
(`Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true })`), dispatch real
`scroll` events, and assert `scrollTop` (see `tests/lib/chat/useBottomAnchoredScroll.test.tsx`).
The REAL pinned-vs-scrolled-up behavior still needs a browser: `tests/e2e/chatUxContract.e2e.mjs`.

**E2E seeding gotcha.** The live feed maps `Story.digest_id = stories.story_id`
(`src/lib/feed/supabaseFeed.ts:159`), NOT the digest row's id — anything keyed "per story" in
localStorage (qa history, voice transcripts) must be seeded under the STORY id in e2e scripts, or
the sheet hydrates empty and the test times out.
