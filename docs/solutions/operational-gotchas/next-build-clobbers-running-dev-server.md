---
title: "`npm run build` clobbers a running `next dev` server — e2e tests start timing out for no reason"
tags: [nextjs, dev-server, e2e, puppeteer, false-negative]
problem_type: operational
symptoms: A puppeteer e2e that was passing suddenly fails with `TimeoutError: Waiting for selector [data-story-index="N"] failed`; the dev server still answers 200 on `/`, and reverting the code change does not bring the test back
root_cause: "`next build` and `next dev` share the same `.next/` directory — running the build while the dev server is up replaces the dev server's chunks out from under it, so the page shell loads (200) but the app never hydrates"
date: 2026-07-18
---

Hit during slice #46's mutation-testing step, and it cost a confusing detour: the test had
been green minutes earlier, the only change was a one-line CSS revert, and `curl` still
returned 200 — which made it look like a real regression in the app rather than a broken
harness.

**The tell:** the server answers 200 but `waitForSelector` times out on an element that
was there before. A 200 on `/` proves only that the shell is served; it does not prove the
JS bundle is intact.

**Rule:** never run `npm run build` while a `next dev` server is running against the same
tree. If you must (e.g. the B8.6 iOS deploy chain needs a production build mid-slice),
sequence it: stop the dev server → build → `rm -rf .next` → restart the dev server before
resuming browser tests.

**Recovery:**
```bash
pkill -f "next dev"; rm -rf .next; PORT=<port> npm run dev &
```
Then wait for a real 200 AND re-run a known-green test before trusting any new failure.

Related: this tree is shared by concurrent agents (see the concurrent-agents note), so
another session's build can do this to your dev server too. If a green test goes red with
no relevant diff, suspect the harness before the code.
