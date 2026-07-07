---
title: .gitignore `build/` swallows ANY new directory named build (git + biome)
tags: [gitignore, biome, build, scripts, check-ignore]
problem_type: tooling
symptoms: new files never appear in `git status`; biome says "These paths were provided but ignored"
root_cause: unanchored `build/` pattern in repo-root .gitignore matches dirs named build at any depth; biome vcs.useIgnoreFile inherits it
---

# .gitignore `build/` swallows ANY new directory named build (git + biome)

**Date:** 2026-07-07 · **Surfaced by:** issue #33 (build stamp + iOS preflight)

## Problem

New source dirs `scripts/build/` and `tests/lib/build/` were silently invisible:
`git status` never showed them (files would never commit) and biome ignored them
even after adding `scripts/build/**` to `biome.json` includes (biome has
`vcs.useIgnoreFile: true`, so `.gitignore` wins).

## Root cause

Repo-root `.gitignore` line 20 is `build/` — an unanchored pattern, so it matches
a directory named `build` at ANY depth, not just the repo-root `build/` output dir.

## Fix / rule

Never name a committed directory `build` in this repo. Issue #33 used
`scripts/ios/` + `tests/lib/ios/` instead. If you see "These paths were provided
but ignored" from biome, or a new file that never appears in `git status`, run
`git check-ignore -v <path>` FIRST — it names the exact ignore line.
