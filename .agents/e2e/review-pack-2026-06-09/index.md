# Human Review Pack — Go-Live Check 2026-06-09

Assembled from **DB truth (service-role queries) + run artifacts only** — the dev server was stopped at assembly time. Feed allocation ran for **feed_date 2026-06-10 (UTC)**. Raw DB dump used for this pack: [`db-truth.json`](db-truth.json). Driver artifacts: `.agents/e2e/state/` (note: some profile dirs contain stale `FAIL-*` files from earlier killed/stale-state attempts — they do **not** belong to the final passing runs; see each profile's verdict file).

## Summary

| Profile | Interests persisted (`user_interest_profile`) | Entity follows (`user_entity_follows`) | Personalized stories (2026-06-10) | Journey verdict |
|---|---|---|---|---|
| profile-a-tech-ai | **0** (all 4 picks are registry entities — expected) | **4** | 0 — global fallback | PASS 9/9 (final run 23:42; earlier attempt killed by infra, see verdict) |
| profile-b-sport | **0** (all 3 picks are registry entities — expected) | **3** | 0 — global fallback | PASS 9/9 |
| profile-c-markets-geo | **3** | **1** | **2** (personalized feed verified live, second pass 2/2) | PASS 9/9 + personalized 2/2 |
| profile-d-arts-mixed | **2** (+1 free-text custom topic unpersisted — expected v1) | **1** | 0 — global fallback | PASS 9/9 |

## Honest caveats (read before reviewing)

1. **The story pool is thin: 7 stories total, only 2 interest-tagged** (`cand-4380e7978d28`, `cand-b90fa40873fe` — both markets/semis). GDELT ingest has been stale since **2026-06-06**, so the allocator had almost nothing to match against.
2. **Why a/b/d got no personalized feed:** profiles a and b persisted **zero** `user_interest_profile` rows (their picks are all entities, and the allocator's "active user" gate requires ≥1 interest row — known gap, flagged in `../state/topic-persistence-rca.md`). Profile d has 2 interest rows (Nuclear, Wildfires) but **zero stories tagged with those interests** in the 7-story pool. All three see the **global fallback feed** (documented `reel_feed_fallback_global` behavior, not a crash).
3. **Topic-taxonomy fix mid-session:** the picker's topic vocabulary was missing from the `interests` table for most of the run (root cause: `../state/topic-persistence-rca.md`). It was seeded mid-session; profiles c and d were re-run afterwards, which is why their topic picks now show as persisted rows below.
4. Profile-c's two `daily_feeds` rows were allocated as **`breaking` slots with no matched interest recorded** (`feed_matched_interest_id` is null) — the ranking scored them against c's profile, but the rows don't carry an interest label.

---

## The global fallback feed (what profiles a, b and d see)

`stories ⋈ digests (digest_is_current)` ordered by `story_id` — the exact query the reel's global path runs (`src/lib/feed/supabaseFeed.ts`). 7 stories:

| # | Story id | Headline | Bucket | Outlet | Duration |
|---|---|---|---|---|---|
| 1 | `cand-4380e7978d28` | Prediction: This Artificial Intelligence Semiconductor Stock Will Outperform Nvidia Over the Next 5 Years | Markets | yahoo.com | 0:58 |
| 2 | `cand-b90fa40873fe` | Intel vs TSMC: Which Semiconductor Giant Offers Stronger Outlook for Investors in 2026 | Markets | ibtimes.com.au | 0:59 |
| 3 | `s1` | U.S. strikes Iran again as Trump says a deal is "close" | Geopolitics | CNN | 0:50 |
| 4 | `s2` | Travis Kelce buys a minority stake in the Cleveland Guardians | Sport | ESPN | 0:46 |
| 5 | `s3` | Houston physicists break a 30-year superconductivity record | Tech & Science | ScienceDaily | 0:55 |
| 6 | `s4` | Nvidia's blowout quarter — yet the stock slips | Markets | CNBC | 0:47 |
| 7 | `s5` | Pope Leo XIV issues his strongest warning yet on AI | Wildcard | TechStartups | 0:47 |

Stories 3–7 (`s1`–`s5`) are the M0 seed set from 2026-05-30; stories 1–2 are the only fresh GDELT-pipeline stories (06-05/06-06).

### Reels (all 7 stories — shared section; a/b/d see exactly this list, c sees stories 1–2 as her personalized feed)

#### `cand-4380e7978d28` — Prediction: This Artificial Intelligence Semiconductor Stock Will Outperform Nvidia Over the Next 5 Years

*Markets · yahoo.com · duration 0:58 (58571 ms)*

![Poster — Prediction: This Artificial Intelligence Semiconductor Stock Will Outperform Nvidia Over the Next 5 Years](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/cand-4380e7978d28/poster.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/cand-4380e7978d28/digest.mp3)

<details><summary>Full transcript (13 sentences)</summary>

> **ALEX:** Jordan, I'm hearing predictions about an AI stock that could outperform Nvidia.
>
> **JORDAN:** What company are we talking about?
>
> **ALEX:** Alex, the AI cloud company CoreWeave is being highlighted as a potential outperformer over the next five years.
>
> **JORDAN:** CoreWeave?
>
> **ALEX:** How are they connected to Nvidia, and what makes them a contender?
>
> **JORDAN:** They're a key Nvidia partner, providing AI cloud services using Nvidia's technology.
>
> **ALEX:** Nvidia even increased its holding in CoreWeave by 95%.
>
> **JORDAN:** And their growth?
>
> **ALEX:** CoreWeave's revenue grew 112% to $2.1 billion in the first quarter of 2026.
>
> **JORDAN:** This is faster than Nvidia's 85% revenue growth in its first quarter, partly due to their smaller size.
>
> **ALEX:** So, faster growth, but any downsides to this rapid expansion?
>
> **JORDAN:** They reported a $740 million loss in the first quarter, and profitability is unlikely soon due to capital expenditures for a $99.4 billion backlog.
>
> **ALEX:** Still, their smaller size aids rapid growth.

</details>

#### `cand-b90fa40873fe` — Intel vs TSMC: Which Semiconductor Giant Offers Stronger Outlook for Investors in 2026

*Markets · ibtimes.com.au · duration 0:59 (59171 ms)*

![Poster — Intel vs TSMC: Which Semiconductor Giant Offers Stronger Outlook for Investors in 2026](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/cand-b90fa40873fe/poster.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/cand-b90fa40873fe/digest.mp3)

<details><summary>Full transcript (12 sentences)</summary>

> **ALEX:** Intel's stock has seen incredible gains this year.
>
> **JORDAN:** What's the outlook for semiconductor investors in the AI boom?
>
> **ALEX:** Intel's shares have delivered over 200% year-to-date gains as of early June 2026, trading around $111 to $112.
>
> **JORDAN:** That's a dramatic turnaround for the U.S.-based chip designer.
>
> **ALEX:** Wow, 200% is huge!
>
> **JORDAN:** How has TSMC, the world's leading chipmaker, performed in comparison?
>
> **ALEX:** TSMC's shares have risen steadily, trading near $445, with strong but less explosive performance than Intel.
>
> **JORDAN:** TSMC holds about 70% market share in advanced foundry services.
>
> **ALEX:** So, they have different market positions.
>
> **JORDAN:** Which company offers a stronger outlook for investors?
>
> **ALEX:** For conservative, long-term investors seeking stability, TSMC stands out as the stronger core holding.
>
> **JORDAN:** Intel offers higher-upside potential for those comfortable with its turnaround risks and willing to tolerate more volatility.

</details>

#### `s1` — U.S. strikes Iran again as Trump says a deal is “close”

*Geopolitics · CNN · duration 0:50 (50611 ms)*

![Poster — U.S. strikes Iran again as Trump says a deal is “close”](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/digest-1.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/digest-1.mp3)

<details><summary>Full transcript (11 sentences)</summary>

> **ALEX:** The U.S. military hit another target inside Iran overnight — a site Washington says threatened American forces and commercial shipping.
>
> **JORDAN:** And President Trump says he's confident a deal to end the fighting is close.
>
> **ALEX:** But "close" isn't "done.
>
> **JORDAN:** Right — he made clear he's not satisfied with the terms yet.
>
> **ALEX:** And he's willing to restart strikes if Iran doesn't meet U.S. demands.
>
> **JORDAN:** Meanwhile Tehran is pushing back hard.
>
> **ALEX:** It just issued new rules for any ship trying to pass through the Strait of Hormuz.
>
> **JORDAN:** That's the chokepoint where about a fifth of the world's oil moves.
>
> **ALEX:** Iran's trying to formalize control over it — defying U.S. warnings.
>
> **JORDAN:** So a ceasefire is being talked up, and a flashpoint is heating up — at the same time.
>
> **ALEX:** We'll see which one wins.

</details>

#### `s2` — Travis Kelce buys a minority stake in the Cleveland Guardians

*Sport · ESPN · duration 0:46 (46000 ms)*

![Poster — Travis Kelce buys a minority stake in the Cleveland Guardians](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/digest-2.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/digest-2.mp3)

<details><summary>Full transcript (10 sentences)</summary>

> **JORDAN:** Travis Kelce is now part-owner of a baseball team.
>
> **ALEX:** The Chiefs tight end just bought a minority stake in the Cleveland Guardians — the team he grew up watching.
>
> **JORDAN:** And this is a hometown story.
>
> **ALEX:** Kelce's from Cleveland Heights — as a kid he'd ride the light rail downtown with his dad to catch games.
>
> **JORDAN:** Before football, he was actually one of the best baseball players in the whole Cleveland area.
>
> **ALEX:** He joins a growing club of active stars buying into the MLB — LeBron with the Red Sox, Giannis with the Brewers... ...and his own teammate Patrick Mahomes, who's already part of the Royals.
>
> **JORDAN:** The size of Kelce's stake?
>
> **ALEX:** Still under wraps.
>
> **JORDAN:** But the message is clear — top athletes aren't just playing the game anymore.
>
> **ALEX:** They're buying it.

</details>

#### `s3` — Houston physicists break a 30-year superconductivity record

*Tech & Science · ScienceDaily · duration 0:55 (55891 ms)*

![Poster — Houston physicists break a 30-year superconductivity record](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/digest-3.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/digest-3.mp3)

<details><summary>Full transcript (10 sentences)</summary>

> **ALEX:** Physicists in Houston just broke a record that stood for more than thirty years.
>
> **JORDAN:** We're talking superconductivity — materials that carry electricity with zero resistance, no energy lost at all.
>
> **ALEX:** The catch has always been the cold.
>
> **JORDAN:** You needed extreme temperatures to make it work.
>
> **ALEX:** The old record, set back in 1993, was 133 Kelvin.
>
> **JORDAN:** The University of Houston team just pushed that to 151 Kelvin — the highest ever at normal, everyday pressure.
>
> **ALEX:** They used a trick called "pressure quenching" — squeeze the material, then lock in the new properties after the pressure is gone.
>
> **JORDAN:** It's still really cold, about minus 122 Celsius — but every degree closer to room temperature matters.
>
> **ALEX:** Because if we ever get there: lossless power grids, faster electronics, better fusion and medical scanners.
>
> **JORDAN:** A thirty-year wall, finally moved.

</details>

#### `s4` — Nvidia’s blowout quarter — yet the stock slips

*Markets · CNBC · duration 0:47 (47492 ms)*

![Poster — Nvidia’s blowout quarter — yet the stock slips](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/digest-4.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/digest-4.mp3)

<details><summary>Full transcript (11 sentences)</summary>

> **JORDAN:** Nvidia just reported earnings — and the AI boom shows no sign of cooling.
>
> **ALEX:** The chipmaker pulled in 81.6 billion dollars in revenue for the quarter.
>
> **JORDAN:** Wall Street had expected about 79.
>
> **ALEX:** The engine is the data-center business — revenue there nearly doubled from a year ago.
>
> **JORDAN:** That's the AI gold rush in a single number.
>
> **ALEX:** Profit beat too: a dollar eighty-seven a share, against forecasts of a dollar seventy-eight.
>
> **JORDAN:** And they're not slowing down — Nvidia guided to 91 billion dollars next quarter, well above estimates.
>
> **ALEX:** They also rewarded shareholders: an 80 billion dollar buyback, and a dividend hiked from a single penny to twenty-five cents.
>
> **JORDAN:** So — a blowout.
>
> **ALEX:** And yet the stock actually slipped afterward.
>
> **JORDAN:** When you're priced for perfection, even great isn't always good enough.

</details>

#### `s5` — Pope Leo XIV issues his strongest warning yet on AI

*Wildcard · TechStartups · duration 0:47 (47371 ms)*

![Poster — Pope Leo XIV issues his strongest warning yet on AI](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/story-posters/digest-5.png)

Audio: [digest audio (mp3)](https://cerfennlcgureyifraqy.supabase.co/storage/v1/object/public/digest-audio/digest-5.mp3)

<details><summary>Full transcript (10 sentences)</summary>

> **ALEX:** The Pope just issued one of his strongest warnings yet — about artificial intelligence.
>
> **JORDAN:** Pope Leo the Fourteenth urged world leaders to slow down the race to deploy AI, and to agree on international safeguards.
>
> **ALEX:** His concern?
>
> **JORDAN:** That unchecked AI could deepen misinformation, destabilize societies... ...and push autonomous weapons past meaningful human control.
>
> **ALEX:** That last one is the line a lot of people quietly fear.
>
> **JORDAN:** It's a striking moment — a moral authority stepping into a debate usually led by engineers and CEOs.
>
> **ALEX:** And it lands the same week a major report said AI will soon be the single biggest force shaping global cybersecurity.
>
> **JORDAN:** Two very different voices.
>
> **ALEX:** The same message.
>
> **JORDAN:** Slow down — before the technology gets ahead of us.

</details>

---

## profile-a-tech-ai

`ashesh.srivastava1234+e2e-profile-a@gmail.com` · user_id `40504599-7107-498c-8752-93395edea85b`

### 1. Interests selected

Picker paths driven (from `scripts/e2e/profiles.json`):

- AI → Foundation models & LLMs → Labs & models → **OpenAI**
- AI → Foundation models & LLMs → Labs & models → **Anthropic**
- AI → AI hardware & compute → Companies & topics → **Nvidia**
- Tech → Semiconductors & chips → Companies → **TSMC**

DB truth — `user_interest_profile`: **0 rows** (correct: all four picks are registry *entities*, not taxonomy topics). `user_entity_follows`: **4 rows**:

| Entity | Kind | Follow path | Source | Weight |
|---|---|---|---|---|
| OpenAI | org | AI → Foundation models & LLMs → OpenAI | seed | 1 |
| Anthropic | org | AI → Foundation models & LLMs → Anthropic | seed | 1 |
| Nvidia | company | AI → AI hardware & compute → Nvidia | seed | 1 |
| TSMC | company | Tech → Semiconductors & chips → TSMC | seed | 1 |

Driver evidence (picker step): `onboarding_completed — profile_count:0, entity_follow_count:4, unpersisted_count:0`.

### 2. Stories shortlisted

**No personalized feed for 2026-06-10 — 0 `daily_feeds` rows.** Why: zero interest rows means the allocator never treats a as an active user (entity-only profiles are skipped — known gap, `../state/topic-persistence-rca.md`), and the pool was too thin anyway (7 stories, 2 interest-tagged, GDELT stale since 06-06). **This profile sees the global fallback feed** — see [the shared global-feed section](#the-global-fallback-feed-what-profiles-a-b-and-d-see) above for the 7 stories and their reels. Notably, 3 of the 7 global stories (CoreWeave/Nvidia, Intel/TSMC, Nvidia quarter) happen to be on-interest for this profile by coincidence of the thin pool.

### 3. The reels

Identical to the shared global feed — all 7 reels embedded in [the shared section](#reels-all-7-stories--shared-section-abd-see-exactly-this-list-c-sees-stories-12-as-her-personalized-feed) above.

### 4. Journey screenshots

| Picker (selected) | Reel | Article |
|---|---|---|
| ![picker selected](../state/profile-a-tech-ai/02-picker-selected.png) | ![reel](../state/profile-a-tech-ai/05-reel.png) | ![article](../state/profile-a-tech-ai/08-article.png) |

| Text Q&A | Live voice responding |
|---|---|
| ![text qa](../state/profile-a-tech-ai/09-text-qa.png) | ![voice](../state/profile-a-tech-ai/10-voice-responding.png) |

(Ignore the `FAIL-*` files in this directory — they are stale artifacts of the externally-killed 23:30 attempt and the subsequent stale-state retry; the final 23:42 run passed 9/9. Full story: `../state/profile-a-tech-ai-verdict.md`.)

### 5. Review it yourself

See [Review it yourself](#review-it-yourself) below — use the profile-a email.

---

## profile-b-sport

`ashesh.srivastava1234+e2e-profile-b@gmail.com` · user_id `d65af13f-0e92-4997-bca6-57e65094da34`

### 1. Interests selected

Picker paths driven:

- Sport → Cricket → Leagues → Indian Premier League (IPL) → Teams you follow → **Mumbai Indians**
- Sport → Cricket → People to follow → **Virat Kohli**
- Sport → Basketball → Leagues → NBA → People to follow → **LeBron James**

DB truth — `user_interest_profile`: **0 rows** (correct: all three picks are registry entities). `user_entity_follows`: **3 rows**:

| Entity | Kind | Follow path | Source | Weight |
|---|---|---|---|---|
| Mumbai Indians | team | Sport → Cricket → Indian Premier League (IPL) → Mumbai Indians | seed | 1 |
| Virat Kohli | person | Sport → Cricket → Virat Kohli | seed | 1 |
| LeBron James | person | Sport → Basketball → NBA → LeBron James | seed | 1 |

Driver evidence (picker step): `onboarding_completed — profile_count:0, entity_follow_count:3, unpersisted_count:0`.

### 2. Stories shortlisted

**No personalized feed for 2026-06-10 — 0 `daily_feeds` rows.** Same two reasons as profile-a: entity-only profile (not "active" to the allocator) + pool too thin (7 stories, only 2 interest-tagged, both markets — zero sport stories exist; GDELT stale since 06-06). **Sees the global fallback feed** ([shared section](#the-global-fallback-feed-what-profiles-a-b-and-d-see)). Only 1 of the 7 global stories is sport (Travis Kelce / Cleveland Guardians) — and it is MLB, not cricket/NBA, so this profile currently gets essentially nothing on-interest. That is an honest content gap, not a ranking bug.

### 3. The reels

Identical to the shared global feed — all 7 reels in [the shared section](#reels-all-7-stories--shared-section-abd-see-exactly-this-list-c-sees-stories-12-as-her-personalized-feed).

### 4. Journey screenshots

| Picker (selected) | Reel | Article |
|---|---|---|
| ![picker selected](../state/profile-b-sport/02-picker-selected.png) | ![reel](../state/profile-b-sport/05-reel.png) | ![article](../state/profile-b-sport/08-article.png) |

| Text Q&A | Live voice responding |
|---|---|
| ![text qa](../state/profile-b-sport/09-text-qa.png) | ![voice](../state/profile-b-sport/10-voice-responding.png) |

### 5. Review it yourself

See [Review it yourself](#review-it-yourself) below — use the profile-b email.

---

## profile-c-markets-geo

`ashesh.srivastava1234+e2e-profile-c@gmail.com` · user_id `b57882f4-947b-463f-afcf-2d6ed063db66` — **the only profile with a personalized feed.**

### 1. Interests selected

Picker paths driven:

- Business → Macroeconomy → Indicators → **Inflation**
- Business → Markets & investing → Asset classes → **Stocks & equities**
- Geopolitics → Sanctions, tariffs & trade → Topics → **China tariffs**
- Geopolitics → Armed conflict & war → Conflicts → **Ukraine–Russia**

DB truth — `user_interest_profile`: **3 rows** (the three topic picks; persisted after the taxonomy seed fix):

| Interest label | Slug | Depth | Weight | Source | Strict |
|---|---|---|---|---|---|
| Inflation | `business.inflation` | 1 | 1.5 | typed | no |
| Stocks & equities | `business.stocks-equities` | 1 | 1.5 | typed | no |
| China tariffs | `geopolitics.china-tariffs` | 1 | 1.5 | typed | no |

`user_entity_follows`: **1 row** (the fourth pick is a registry entity):

| Entity | Kind | Follow path | Source | Weight |
|---|---|---|---|---|
| Ukraine–Russia | conflict | Geopolitics → Armed conflict & war → Ukraine–Russia | seed | 1 |

Note: c's *first-pass* run (pre-taxonomy-fix, 23:31) logged `profile_count:0, entity_follow_count:1, unpersisted_count:3`; after the `interests` seed was applied, the re-run persisted all three topics — the table above is current DB truth.

### 2. Stories shortlisted — ranked `daily_feeds` for 2026-06-10

| Pos | Story id | Headline | Bucket | Slot kind | feed_score | Matched interest |
|---|---|---|---|---|---|---|
| 1 | `cand-4380e7978d28` | Prediction: This Artificial Intelligence Semiconductor Stock Will Outperform Nvidia Over the Next 5 Years | markets | breaking | 0.2149 | — (none recorded) |
| 2 | `cand-b90fa40873fe` | Intel vs TSMC: Which Semiconductor Giant Offers Stronger Outlook for Investors in 2026 | markets | breaking | 0.1819 | — (none recorded) |

Honest note: both rows were allocated as **`breaking`** slots and `feed_matched_interest_id` is **null** — no interest label is recorded on either row, even though both stories are plausibly on-interest for "Stocks & equities". Only 2 rows (not ~30) because the pool had just 2 interest-tagged stories.

The second-pass driver verified this live: reel position 1 matched `daily_feeds` ("Prediction: This Artificial Intelligence Semiconductor Stock Will Outperform Nvi…"), no global fallback — `../state/profile-c-markets-geo-result.json`.

### 3. The reels

Profile-c's two personalized reels are stories **1 and 2 of the shared reel section** above (`cand-4380e7978d28` and `cand-b90fa40873fe`) — posters, audio links, durations (0:58 / 0:59) and full transcripts are embedded there.

### 4. Journey screenshots

| Picker (selected) | Reel (first pass, global) | Article |
|---|---|---|
| ![picker selected](../state/profile-c-markets-geo/02-picker-selected.png) | ![reel](../state/profile-c-markets-geo/05-reel.png) | ![article](../state/profile-c-markets-geo/08-article.png) |

| Text Q&A | Live voice responding | **Personalized reel (second pass)** |
|---|---|---|
| ![text qa](../state/profile-c-markets-geo/09-text-qa.png) | ![voice](../state/profile-c-markets-geo/10-voice-responding.png) | ![personalized reel](../state/profile-c-markets-geo/11-personalized-reel.png) |

(`FAIL-*` files in this directory are stale from a pre-reseed 23:14 fixture-feed run — ignore; see `../state/profile-c-markets-geo-verdict.md`.)

### 5. Review it yourself

See [Review it yourself](#review-it-yourself) below — use the profile-c email. This is the profile to review to see **personalization working**.

---

## profile-d-arts-mixed

`ashesh.srivastava1234+e2e-profile-d@gmail.com` · user_id `e3c311e2-c9f5-4fda-b22d-4b2eddf06fdf`

### 1. Interests selected

Picker paths driven:

- Arts → Music → Pick genres, then artists → Pop → Artists & bands → **Taylor Swift**
- Environment → Renewable energy & transition → Topics → **Nuclear**
- Environment → Extreme weather & disasters → Topics → **Wildfires**
- Custom free-text topic: Environment → Conservation & biodiversity → Topics → **"Urban beekeeping"** (typed by the driver)

DB truth — `user_interest_profile`: **2 rows**:

| Interest label | Slug | Depth | Weight | Source | Strict |
|---|---|---|---|---|---|
| Nuclear | `environment.nuclear` | 1 | 1.5 | typed | no |
| Wildfires | `environment.wildfires` | 1 | 1.5 | typed | no |

`user_entity_follows`: **1 row**:

| Entity | Kind | Follow path | Source | Weight |
|---|---|---|---|---|
| Taylor Swift | person | Arts → Music → Pop → Taylor Swift | seed | 1 |

Driver evidence (picker step, final 23:50 run): `onboarding_completed — profile_count:2, entity_follow_count:1, unpersisted_count:1`. The 1 unpersisted item is the free-text "Urban beekeeping" — **expected v1 behavior** (free-text picks have no registry/taxonomy row to attach to; surfaced as unpersisted rather than silently dropped).

### 2. Stories shortlisted

**No personalized feed for 2026-06-10 — 0 `daily_feeds` rows.** d *does* have interest rows (unlike a/b), but the 7-story pool contains **zero stories tagged Nuclear, Wildfires, or anything arts/environment** — the only 2 interest-tagged stories are markets/semiconductors. Pool too thin: GDELT ingest stale since 06-06. **Sees the global fallback feed** ([shared section](#the-global-fallback-feed-what-profiles-a-b-and-d-see)); nothing in it matches d's interests.

### 3. The reels

Identical to the shared global feed — all 7 reels in [the shared section](#reels-all-7-stories--shared-section-abd-see-exactly-this-list-c-sees-stories-12-as-her-personalized-feed).

### 4. Journey screenshots

| Picker (selected) | Reel | Article |
|---|---|---|
| ![picker selected](../state/profile-d-arts-mixed/02-picker-selected.png) | ![reel](../state/profile-d-arts-mixed/05-reel.png) | ![article](../state/profile-d-arts-mixed/08-article.png) |

| Text Q&A | Live voice responding |
|---|---|
| ![text qa](../state/profile-d-arts-mixed/09-text-qa.png) | ![voice](../state/profile-d-arts-mixed/10-voice-responding.png) |

### 5. Review it yourself

See [Review it yourself](#review-it-yourself) below — use the profile-d email.

---

## Review it yourself

The dev server is currently **stopped**. To review live, first restart it from the repo root:

```bash
npm run dev
```

then open http://localhost:3000.

**Important sign-in caveat:** the app UI only offers **magic-link** sign-in. The test users' passwords (in `.agents/e2e/state/test-users.json` — gitignored, holds credentials, not reproduced here) are **harness-only**: `drive-profile.ts` signs in with them via the Supabase API, but there is no password field in the UI. So you have two honest options:

1. **Magic link (real UI path):** enter the profile's email (e.g. `ashesh.srivastava1234+e2e-profile-c@gmail.com`) on the sign-in screen. All `+e2e-*` addresses deliver to your own Gmail (`ashesh.srivastava1234@gmail.com`) — open the magic link from there and you are in as that profile.
2. **Watch the driver live (headed):** re-run the harness with a visible browser and watch the whole journey:
   ```bash
   npx tsx scripts/e2e/drive-profile.ts --profile profile-c-markets-geo --headed
   ```
   (substitute any profile name; note a fresh driver run re-walks onboarding, and re-seeding via `seed-test-users.ts` resets ALL four profiles' personalization rows).

**iOS:** the app is also installed in the currently-open iOS simulator — sign in there the same magic-link way to review reels on-device.

To see personalization specifically, review **profile-c** (the only profile with `daily_feeds` rows for 2026-06-10) — its reel should open directly on the CoreWeave/Nvidia story, not the global feed.
