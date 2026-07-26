# M1 live audit (issue #47) — 2026-07-25 (TWO runs: pre-#70 **FAIL 34/55**, post-#70 **FAIL 58/65 strict / PASS 63/65 lenient**)

This file holds **both** of 2026-07-25's audits, kept side by side for the delta:

- **Part 1 — PRE-#70 run** (~11:09–11:29 local): 55 rows, 34/55 honest (61.8%) → FAIL. Original
  record, unedited, below.
- **Part 2 — POST-#70 re-run** (20:19–20:41 local): 65 rows, 58/65 strict (89.2%) → still FAIL
  against the 93.3% gate, but 63/65 (96.9%) counting marginals. Appended at the bottom.

The pre-#70 artifact is preserved at `.agents/shortlists/2026-07-25-shortlist.pre70.json`; the
post-#70 run overwrote `.agents/shortlists/2026-07-25-shortlist.json`.

---

# Part 1 — PRE-#70 run: batch ran, audit done, **FAIL on tag honesty (34/55 strict)**

The batch #47 gates on ran this morning (2026-07-25, ~11:09–11:29 local) in `SHORTLIST_ONLY`
mode and produced `.agents/shortlists/2026-07-25-shortlist.json` — **55 rows**. Per founder
directive (2026-07-25) this audit judges THAT artifact; no fresh batch was run, no Gemini
call was made by the audit itself, and nothing was produced (no reels, no TTS, no posters).
Repo state: branch `claude/feed-source-revamp-plan-388edf`, HEAD `7d85abf` at audit time.

## Verdict

| Check | Result |
|---|---|
| Fresh founder batch executed | **YES** — shortlist-only, feed_date=2026-07-25, scoped to ash@gmail.com |
| Run mode semantic (not lexical-only fallback) | **PROVEN** — run log found; zero `semantic_relevance_embed_failed`; positive `semantic_relevance_key_completed` (see Evidence) |
| ≥28/30 honest tags → mapped to ≥52/55 (93.3%) | **FAIL** — 34/55 strict (61.8%); 41/55 counting marginals as honest (74.5%). English-only (#63-adjusted): 26/44 (59.1%) strict, 31/44 (70.5%) lenient |
| Zero masthead/fragment headlines | **PASS (literal)** — 0/55 are mastheads or fragments; residuals: 5 rows carry trailing outlet-suffix junk, 10 rows carry unescaped HTML entities (filed as #71) |
| Batch-wide: non-zero ai/business/environment/politics/arts | **PASS (literal) / hollow on environment** — ai 1, business 14, environment 2, politics 4, arts 6; but BOTH environment rows are mis-tags (honest environment count = 0) |
| Zero `wildcard`/`markets` segments | **PASS** — 0 and 0 |
| Chip/headline on separate lines, on-device | **DEFERRED** — text-only directive; production + device build pending explicit founder go |
| Py/TS twin tests green | **PASS, scoped** — `pytest tests/agents/pipeline` = 631 passed; `npx vitest run` = 724 passed / 90 files. Repo-wide pytest still carries the 1 pre-existing inherited failure (`tests/agents/worker/test_interview_route.py::test_deeper_turn_returns_terminal_with_gemini_mocked`) — recorded, not fixed, out of contract scope |
| Error/boundary honesty (Rule 12) | **PASS** — failure filed with offending rows below; batch NOT re-run; fixes filed as issues (#69, #70, #71), not hand-tuned |

**Consequence:** #47 stays OPEN. The tag-honesty criterion FAILED and the on-device
criterion is deferred. Per the issue's own boundary rule, the batch is not re-run until a
code fix lands (#70 is the direct blocker; #69 hygiene guard and #71 are adjacent).

## Threshold mapping (stated per directive)

The issue's criterion is ≥28/30 honest tags on a produced 30-row briefing. Under the
text-only directive the auditable artifact is the 55-row selected candidate pool, so the
criterion maps to a rate: 28/30 = 93.33%, i.e. **≥52 of 55 rows** must be honest
(⌈55 × 0.9333⌉ = 52). On the #63-adjusted English-only pool: ≥42 of 44.

Verdict buckets: **OK** (clearly right or defensible), **MARG** (genuinely arguable —
counted as NOT honest in the strict rate, honest in the lenient rate), **BAD** (clearly
wrong). Both rates are reported; the strict rate is the one held against the criterion.

## Run provenance (disclosed in full)

- **Run 1** (~11:02 local): completed with **0 candidates / 0 shortlist rows**. Root cause
  (filed as #69): the FSR v2 catalog seeded comma-less keyword-soup
  `interest_search_query` values, which the #50 lexical key parses as one unmatchable
  contiguous phrase — every one of ash's 26 followed interests silently ingested nothing.
- **Between runs** (~11:08): the running session rewrote ash's 26 `interest_search_query`
  rows in prod to the comma-anchor contract (originals snapshotted to the session
  scratchpad, `interest-query-snapshot-2026-07-25.json`). This is input repair upstream of
  selection — interest queries, not shortlist rows — and no shortlist row was edited,
  removed, or re-labeled at any point.
- **Run 2** (11:09–11:29): `RUN_LIVE_BATCH=1 SHORTLIST_ONLY=1` scoped to ash@gmail.com,
  feed_date=2026-07-25, lookback 1 day. 993 stories through the semantic key → 661
  candidates → 543 skipped by gate, 63 capped → **55 shortlisted**. `produced=0
  feeds_written=0`.
- **Prod check after the run:** `daily_feeds` rows for 2026-07-25: **0**; for 2026-07-24:
  **0**. Shortlist-first honored; the produce-once gate is not armed for today.

## Evidence — run mode is proven, not inferred

The 07-24 doc required grepping the run log for `semantic_relevance_embed_failed` before
trusting any shortlist. The log was found (session scratchpad
`/private/tmp/claude-501/-Users-asheshsrivastava-News20-News20/61e639c8-457f-4ea5-8b9f-2d3f0c545c16/scratchpad/shortlist-run2-2026-07-25.log`,
1.6 MB, mtime 11:29 — matching the artifact's mtime):

- `semantic_relevance_embed_failed`: **0 occurrences**; zero error-level events in the log.
- Positive proof the key ran (quoted verbatim, since the scratchpad log is ephemeral —
  #67, stamping this into the artifact itself, is still open):

```
{"stories_checked": 993, "interests_checked": 25, "pairs_evaluated": 1020,
 "pairs_rejected": 189, "embed_call_count": 11, "embed_char_count": 389027,
 "embeddings_per_story": 1.025, "threshold": 0.5,
 "event": "semantic_relevance_key_completed", ...
 "timestamp": "2026-07-25T16:28:40.523367Z"}
```

- 21 HTTP POSTs to `gemini-embedding-001:batchEmbedContents`, all logged 200; semantic
  clustering also ran (`story_clustering_completed`, 323 joins / 661 spawns).

Corroborating (inference, not needed for the proof): the degraded-looking 07-20 artifact
was 10/19 non-English (53%); today is 11/55 (20%).

## Row-by-row audit (55 rows in, 55 verdicts out)

Columns: **Tag** = category matches story subject (OK/MARG/BAD) · **Hl** = headline
informative, non-masthead, non-fragment (OK; SUF = trailing outlet-suffix junk; ENT =
unescaped HTML entities — SUF/ENT still count as literal-criterion passes) · **En** =
English (N rows are #63 failures, reported not excused) · **Int** = story plausibly
relevant to ≥1 matched interest slug (OK/BAD; — = untagged row; `*` = passes but carries
phantom extra slugs, see #70).

| # | Cat | Matched slugs | Headline (truncated) | Tag | Hl | En | Int | Reason (non-OK verdicts) |
|---|---|---|---|---|---|---|---|---|
| 1 | tech | tech | Can an app fix the 'friendship recession'? | OK | OK | Y | OK | |
| 2 | arts | business, environment, politics, tech | 'The Odyssey' Tops 'Oppenheimer' Box Office Opening With $84.5M… | OK | OK | Y | OK* | phantom extras: environment/politics/tech |
| 3 | business | business, politics, tech | 'Dhamaal 4' box office collection Day 16 (Live): Ajay Devgn… | MARG | OK | Y | OK* | fan-service box-office tracker — arts more natural (cf. row 2 tagged arts); phantom politics/tech |
| 4 | arts | — | 2026 AFF Cup Garuda Home Matches: Ticket Sales, Price List… | BAD | OK | Y | — | football-tournament tickets chipped arts (should be sport) |
| 5 | arts | — | MLS Teams See Ticket Sales Jump More Than 150% After World Cup | BAD | OK | Y | — | sports-business story chipped arts |
| 6 | business | business | China's summer box office heats up, filmmakers push beyond… | OK | OK | Y | OK | industry-trend framing |
| 7 | arts | politics | Manoj Muntashir calls Adipurush his "biggest mistake"… | OK | SUF | Y | BAD | ": Bollywood News" suffix; sole matched slug politics fits nothing — the arts.bollywood follow that plainly surfaced it is absent |
| 8 | business | business | Paramount Postpones Warner Bros. Discovery Merger | OK | OK | Y | OK | |
| 9 | geopolitics | business | [zh] 第二艘国产18万方超大型LNG船交付，可运输零下163℃… | MARG | ENT | N(zh) | OK | LNG-carrier delivery = industry/business; energy-security framing arguable; #63 |
| 10 | business | business, politics | Kuwait Oil inks $16bn pipeline lease deal with investor… | OK | OK | Y | OK | |
| 11 | business | business, tech | Meta Exits Renewable Energy Initiative as Natural Gas Data… | OK | OK | Y | OK | |
| 12 | geopolitics | business, politics | Province bypasses Utilities Commission on key certificate for… | MARG | OK | Y | OK | domestic Canadian regulatory story — politics closer than geopolitics |
| 13 | geopolitics | — | Firefighters, Oklahoma Natural Gas respond to gas leak in… | BAD | OK | Y | — | hyper-local incident chipped geopolitics |
| 14 | business | business | Economy bringing mixed messages ahead of next federal reserve… | OK | OK | Y | OK | |
| 15 | business | business | Inflation, economy cited as shoplifting rises, survey says | OK | OK | Y | OK | |
| 16 | politics | politics | The 'rockets and feathers' effect: Inflation eases but grocery… | MARG | OK | Y | OK | economics story chipped politics; cost-of-living framing arguable |
| 17 | sport | — | [es] Los diez hechos que marcan el comienzo de la Liga BetPlay… | OK | ENT | N(es) | — | #63 |
| 18 | sport | — | [es] De las inferiores del América a la presidencia de la Liga MX… | OK | ENT | N(es) | — | #63 |
| 19 | environment | environment, politics | Maeda: Happy to Fulfil Premier League Dream With Town -… | BAD | SUF | Y | BAD | football transfer chipped environment; both slugs phantom; "- Ipswich Town News" suffix |
| 20 | sport | — | [es] Llaneros inició con triunfo la Liga BetPlay 2026-II… | OK | ENT | N(es) | — | #63 |
| 21 | business | business | UN debate puts spotlight on critical minerals governance –… | OK | SUF | Y | OK | "– Channel Africa" suffix |
| 22 | tech | tech | Study links household drinking water to long-term lithium… | MARG | OK | Y | BAD | health study; taxonomy has no science bucket, tech nearest; tech slug spurious (lithium anchor hit) |
| 23 | business | business | Stocks waver while crude oil prices fall for the first time… | OK | OK | Y | OK | |
| 24 | business | business, geopolitics | U.S. Dollar Ticks Higher As U.S. Steps Up Threats On Iran… | OK | OK | Y | OK | |
| 25 | geopolitics | business, geopolitics | Oil Prices Dip Below $100 as Middle East Risk Weighs | OK | OK | Y | OK | |
| 26 | sport | — | Vishmi, Harshitha centuries set up ODI series decider | OK | OK | Y | — | |
| 27 | tech | tech | Pakistan opener Abdullah Fazal ruled out of West Indies Test… | BAD | OK | Y | BAD | cricket injury chipped tech; tech slug phantom |
| 28 | sport | — | WI out to honour Sir Garry against Pakistan | OK | OK | Y | — | |
| 29 | sport | — | India to Announce Test Squad by Tuesday: Saikia | OK | OK | Y | — | |
| 30 | environment | business, environment, politics, tech | [it] Musk celebra il volo di prova di Starship mentre la Nasa… | BAD | OK | N(it) | OK* | Starship test chipped environment; tech plausible (tech.launches-missions), 3 phantom extras; #63 |
| 31 | politics | business, environment, politics | OpenAI and Hugging Face partner to address security incident… | BAD | OK | Y | OK* | AI-industry story chipped politics while an ai chip exists (row 38); environment phantom |
| 32 | politics | politics | Mapping rural data center development finds growing opposition | OK | OK | Y | OK | |
| 33 | sport | — | Srikkanth wants Siraj to be picked up for 2027WC; highlights… | OK | OK | Y | — | |
| 34 | geopolitics | geopolitics | With swords, axes and shields, fighters go head-to-head in… | BAD | OK | Y | BAD | medieval-combat championship chipped geopolitics; slug phantom |
| 35 | business | business | Tyson Foods completes Lexington plant closure Friday with… | OK | OK | Y | OK | |
| 36 | tech | tech | Abhishek Bachchan and Aishwarya Rai spotted clicking pictures… | BAD | ENT | Y | BAD | celebrity sighting chipped tech; slug phantom; `&#xA0;` entity in stored title |
| 37 | tech | politics, tech | Dharmendra Pradhan Resigns From Education Ministry; Bollywood… | BAD | OK | Y | OK* | minister resignation chipped tech (politics is right and IS matched); tech phantom |
| 38 | ai | geopolitics, tech | Did Chinese AI Steal From Anthropic, and OpenAI Loses Control… | OK | OK | Y | OK | newsletter-style double-header, still informative |
| 39 | politics | politics | OpenAI incident prompts White House scrutiny, new AI bills | OK | OK | Y | OK | |
| 40 | sport | — | Cabo Verde goalie Vozinha headed to Chile post FIFA World Cup… | OK | OK | Y | — | |
| 41 | sport | — | Spain coach De la Fuente says Argentina's conduct after World… | OK | OK | Y | — | |
| 42 | sport | — | Shakira and Burna Boy's Dai Dai Hits New Streaming High After… | MARG | SUF | Y | — | music story on sport chip (World-Cup-anthem crossover); "\| Alice 107.7" suffix |
| 43 | tech | tech | Justin Bieber Reportedly Lands Super Bowl 2027 Role After… | BAD | OK | Y | BAD | entertainment story chipped tech; slug phantom |
| 44 | business | business | Short Interest in Bitwise Trendwise Bitcoin and Treasuries… | OK | OK | Y | OK | programmatic finance content — thin but honestly tagged |
| 45 | arts | — | Maya Jama 'set to sign on for third series of hit Netflix show'… | OK | OK | Y | — | |
| 46 | arts | — | Weekend Watchlist: What's new in theaters, on streaming | OK | OK | Y | — | weak roundup teaser, but not masthead/fragment |
| 47 | tech | tech | Celebrity giving is evolving: Ariana Grande's foundation shows… | BAD | OK | Y | BAD | celebrity philanthropy chipped tech; slug phantom |
| 48 | geopolitics | geopolitics | 3 Courteney Cox Horror Movies Including Scream Join Netflix… | BAD | OK | Y | BAD | streaming listicle chipped geopolitics; slug phantom |
| 49 | geopolitics | geopolitics | Steven Knight's Oasis documentary to premiere at Venice Film… | BAD | OK | Y | BAD | arts story chipped geopolitics; slug phantom |
| 50 | sport | — | [nl] Volgens Trump is dé perfecte VN-chef al gevonden: FIFA-… | MARG | ENT | N(nl) | — | Trump-proposes-Infantino-for-UN — politics/sport crossover; #63 |
| 51 | sport | politics | [id] IOC Tolak Selidiki Presiden FIFA Gianni Infantino Terkait… | OK | OK | N(id) | OK | sports-governance; politics plausible; #63 |
| 52 | sport | politics | [es] Federación Noruega estudia denunciar a Gianni Infantino… | OK | ENT | N(es) | OK | #63 |
| 53 | sport | — | [ro] Se solicită rejucarea finalei Cupei Mondiale: Ce șanse… | OK | ENT | N(ro) | — | #63 |
| 54 | business | business, politics | [zh] 河南GDP领跑全国5%增速 高技术制造业迎重大投资机遇- CFi.CN 中财网 | OK | SUF+ENT | N(zh) | OK | "- CFi.CN 中财网" suffix; #63 |
| 55 | business | business | [vi] Chỉ trong nửa năm, công ty mẹ Google thu hơn 6 triệu tỷ… | OK | ENT | N(vi) | OK | #63 |

## Batch-wide arithmetic (all sums checked against 55)

**Category counts:** ai 1 · arts 6 · business 14 · environment 2 · geopolitics 7 ·
politics 4 · sport 14 · tech 7 — total 55. `wildcard` 0, `markets` 0.

**Tag honesty:** OK 34 (rows 1, 2, 6, 7, 8, 10, 11, 14, 15, 17, 18, 20, 21, 23, 24, 25,
26, 28, 29, 32, 33, 35, 38, 39, 40, 41, 44, 45, 46, 51, 52, 53, 54, 55) · MARG 7 (3, 9,
12, 16, 22, 42, 50) · BAD 14 (4, 5, 13, 19, 27, 30, 31, 34, 36, 37, 43, 47, 48, 49).

- Raw strict rate: **34/55 = 61.8%** (needed ≥52/55 = 93.3%) → **FAIL**
- Raw lenient rate (MARG as honest): 41/55 = 74.5% → still FAIL
- Non-English rows (#63-attributable, reported not excused): 11 (rows 9, 17, 18, 20, 30,
  50, 51, 52, 53, 54, 55 — es×4, zh×2, it, nl, id, ro, vi). English share 44/55 = 80%.
- #63-adjusted (English-only, 44 rows): OK 26 · MARG 5 · BAD 13 → strict **26/44 = 59.1%**,
  lenient 31/44 = 70.5% → **FAIL — the #63 adjustment does not rescue the rate; 13 of the
  14 clearly-wrong tags are on English rows.**

**Headlines:** pure masthead or fragment: **0/55**. Residual defects (filed #71): trailing
outlet-suffix junk 5 rows (7, 19, 21, 42, 54); unescaped HTML entities in stored titles 10
rows (9, 17, 18, 20, 36, 50, 52, 53, 54, 55).

**Interest-match plausibility:** untagged 17 rows (4, 5, 13, 17, 18, 20, 26, 28, 29, 33,
40, 41, 42, 45, 46, 50, 53 — matches the run log's `untagged_count: 17`). Of the 38 tagged
rows: plausible ≥1 slug 28 · implausible 10 (7, 19, 22, 27, 34, 36, 43, 47, 48, 49).
Several passing rows additionally carry phantom extra slugs (2, 3, 30, 31, 37).

**Attribution anomaly (evidence for #70):** every matched slug in the artifact is a
segment-level root (tech/business/environment/politics/geopolitics). Not one of ash's 26
followed leaf interests (arts.bollywood, sport.fifa, business.inflation, …) appears,
although `shortlist.py` documents the field as "LEAF-matched (depth-0) interests whose
queries surfaced this story" — and ash follows no `environment` or `politics` root at all.
The environment category count (2) is produced entirely by this failure: honest
environment stories in the pool = **0**.

## Delta from #47's literal wording

Unchanged from the 07-24 doc: the shortlist is the *selected candidate pool* (headline,
outlet count, resolved category, matched interests), not a 30-row produced briefing. Under
the text-only directive that pool is what the tag-honesty and headline criteria are judged
on (threshold mapped as above); the on-device chip/headline criterion needs a full
producing run plus a device build and stays deferred pending explicit founder go.

## Defects (all filed, none fixed inline)

- **#69** (pre-filed by the run session): catalog `interest_search_query` values violate
  the #50 comma-anchor contract — cause of run 1's zero candidates; ~94+ unfollowed
  catalog interests still broken.
- **#70** (pre-filed by the run session): scrambled category chips + phantom root
  matched slugs — the direct cause of this audit's FAIL; this doc's row table is the
  fixture list.
- **#71** (filed by this audit): unescaped HTML entities + trailing outlet-suffix junk
  survive to canonical titles.
- **#63** (known gap, open): 11/55 non-English rows.
- **#67** (open): run-mode flag still absent from the artifact — this audit's mode proof
  had to come from an ephemeral scratchpad log, quoted above for durability.

## Next step

The single remaining #47 gate is production + on-device verification (chip/headline on
separate lines), which requires an explicit founder production go — but a producing run is
pointless until #70 lands: 21/55 rows of this pool would render with wrong or junk chips.
Recommended order: fix #70 (and ideally #71) → fresh SHORTLIST_ONLY run → re-audit →
founder go → produce → on-device check.

---

# Part 2 — POST-#70 re-run (2026-07-25 20:19–20:41 local): **FAIL 58/65 strict (89.2%), PASS 63/65 lenient (96.9%)**

Fresh `SHORTLIST_ONLY` batch on the **post-#70 code** (HEAD `e55faa6`, branch
`claude/feed-source-revamp-plan-388edf`) to measure whether c38092f + e55faa6 fixed the
scrambled chips / phantom root slugs that caused Part 1's FAIL. Founder directive for this
run: **shortlist only — no images, no speech, no story text.** Enforced by
`SHORTLIST_ONLY=1 DISABLE_POSTER_GEN=1 MAX_PRODUCE=0`; the halt was verified in source to sit
immediately above `_produce_story_pool` (script LLM → TTS → posters), so zero text/audio/image
was generated. `poster mode ... DISABLED (DISABLE_POSTER_GEN kill switch — zero image calls)`
is in the run log.

## Verdict

| Check | Result |
|---|---|
| Fresh founder batch executed | **YES** — shortlist-only, feed_date=2026-07-25, scoped to ash@gmail.com |
| Run mode semantic (not lexical-only fallback) | **PROVEN by positive evidence** — `semantic_relevance_key_completed` with 1009 stories / 11 embed calls; 21× `batchEmbedContents` 200 OK; `semantic_relevance_embed_failed` = 0, `semantic_relevance_key_inactive` = 0, error-level events = 0 |
| ≥28/30 honest tags → mapped to ≥61/65 (93.3%) | **FAIL (strict) 58/65 = 89.2%**, short by 3 rows. **PASS (lenient, marginals honest) 63/65 = 96.9%**. English-only (#63-adjusted, 50 rows): strict 45/50 = 90.0% (gate 47) → FAIL; lenient 48/50 = 96.0% → PASS |
| Zero masthead/fragment headlines | **PASS (literal)** — 0/65 are pure mastheads or fragments; residuals: 15 rows carry unescaped HTML entities, 8 rows trailing outlet-suffix junk, 1 row (33) a concatenated double-title with the outlet name fused (all #71) |
| Batch-wide: non-zero ai/business/environment/politics/arts | **PARTIAL FAIL** — ai 14, business 14, arts 8 (all non-zero, all honest); **environment 0, politics 0**. Cause is NOT a bug: ash follows **zero** environment and **zero** politics interests (26 followed, verified in prod). Part 1's environment 2 / politics 4 were produced entirely by the #70 phantom-root bug — honest environment stories in that pool were 0. This criterion is unsatisfiable for this profile without either the bug or new follows |
| Zero `wildcard`/`markets` segments | **PASS** — 0 and 0 |
| Chip/headline on separate lines, on-device | **OUT OF SCOPE this run** — no produced briefing and no device build under the shortlist-only / no-text directive. NOT claimed as passed |
| Py/TS twin tests green | **PASS, scoped** — `pytest tests/agents/pipeline` = 656 passed; `npx vitest run` = 724 passed / 90 files. Repo-wide `pytest tests` = 1349 passed, 1 skipped, **1 pre-existing inherited failure** (`tests/agents/worker/test_interview_route.py::test_deeper_turn_returns_terminal_with_gemini_mocked`) — identical to the 07-24/07-25 records, not caused by this run |
| Error/boundary honesty (Rule 12) | **PASS** — failure reported with offending rows; no row relabelled or hand-removed; residuals filed against existing issues |

**Consequence:** #47 stays **OPEN**. The strict tag-honesty gate failed by 3 rows and the
on-device criterion is out of scope. #70 is nonetheless **decisively validated** (see delta).

## Run provenance

- Started 20:19:04 local (2026-07-26T01:19:04Z), halted 20:41:50 local (01:41:50Z), ~23 min.
- Credits: re-probed before the run with one `embed_texts` call — HTTP 200,
  `gemini-embedding-001`, 768-d. `.env` mtime unchanged (`Jul 19 11:56:37 2026`), so the key
  was **not** rotated; billing was funded on the existing key.
- Pipeline: 1376 candidates → **1009 canonical stories** (85 multi-outlet) → semantic relevance
  key (1054 pairs evaluated, 230 rejected, threshold 0.5) → 670 candidates → 498 skipped by
  gate, 107 capped → **65 shortlisted**. `produced=0`, `feeds_written=0`.
- **Dates (straddle, reported honestly):** the artifact is stamped **feed_date `2026-07-25`**
  (script uses `date.today()` in *local* time). The GDELT pull is **not** a calendar day — it
  is a rolling window `now_utc − LOOKBACK_DAYS`, i.e. **2026-07-25T01:19Z → 2026-07-26T01:19Z**.
  So a 2026-07-25-stamped shortlist contains stories from both UTC 07-25 and UTC 07-26.
  Acceptable for a tag-honesty audit; see
  `docs/solutions/operational-gotchas/batch-straddling-utc-midnight-splits-pull-day.md`.
- **Prod writes: none.** `story_interests` rows with `story_interest_created_at >= 2026-07-25T00:00:00Z`:
  **0** (checked after the run — confirms the shortlist-only path writes no tags, relevant to #72's
  concurrent cleanup).

## Evidence — run mode proven, not inferred

Per `docs/solutions/…/proving-run-mode-needs-positive-log-evidence.md`, absence of the error is
not proof. The positive line, verbatim from `/tmp/m1-audit-post70.log`:

```
{"stories_checked": 1009, "interests_checked": 24, "pairs_evaluated": 1054,
 "pairs_rejected": 230, "embed_call_count": 11, "embed_char_count": 397238,
 "embeddings_per_story": 1.024, "threshold": 0.5,
 "event": "semantic_relevance_key_completed", "level": "info",
 "logger": "ingestion.interest_semantic",
 "timestamp": "2026-07-26T01:40:04.029839Z"}
```

Plus 21 × `POST …/gemini-embedding-001:batchEmbedContents "HTTP/1.1 200 OK"`, and
`story_clustering_completed` (1376 → 1009). Zero error-level events in a 4570-line log.

## The #70 fix is decisively validated

Three independent structural signals, all from the artifact/log rather than judgment:

| Signal | Pre-#70 | Post-#70 |
|---|---|---|
| `untagged_count` (rows with no matched slug) | **17 / 55** | **0 / 65** |
| Matched slugs that are real followed **leaf** interests | **0** — every slug was a bare segment root (`tech`, `business`, `environment`, `politics`, `geopolitics`) | **22 / 22 distinct slugs**, all ⊆ ash's 26 followed interests (`sport.cricket.india`, `ai.compute-energy-demand`, `arts.bollywood`, `tech.launches-missions`, …) |
| `shortlist_matched_slug_outside_followed_set` warnings | n/a (invariant not yet built) | **0** |

The specific scrambled rows from Part 1 are fixed. Same stories, this run:

- cricket injury (Abdullah Fazal) — was **tech**, now **sport** (`sport.cricket`) — row 32
- Bollywood celebrity sighting (Abhishek Bachchan / Aishwarya Rai) — was **tech**, now **arts**
  (`arts.bollywood`) — row 43
- Netflix / streaming items — were **geopolitics**, now **arts** (`entertainment`) — rows 58, 59
- Starship test flight — was **environment**, now **tech** (`tech.launches-missions`) — row 34
- football transfer — was **environment**, now **sport** (`sport.soccer`) — row 20

## Row-by-row audit (65 rows in, 65 verdicts out)

Columns as Part 1. **Tag** OK / MARG (arguable — counted NOT honest in strict, honest in
lenient) / BAD (clearly wrong) · **Hl** OK / SUF (outlet-suffix junk) / ENT (HTML entities) /
CAT (concatenated double-title) · **En** English? · **Int** ≥1 matched slug plausible?

| # | Cat | Matched slugs | Headline (truncated) | Tag | Hl | En | Int | Reason (non-OK) |
|---|---|---|---|---|---|---|---|---|
| 1 | business | ai.catastrophic-risk, business.jobs | Amazon cuts jobs within artificial intelligence team | OK | OK | Y | OK | |
| 2 | ai | ai.compute-energy-demand, business.jobs | Trump expands a voluntary pledge … from AI data centers \| News, Sports, Jobs | OK | SUF | Y | OK | "\| News, Sports, Jobs" masthead suffix |
| 3 | arts | arts.box-office | The Odyssey Full Movie Collection: 'The Odyssey' box office collection day 9 … | OK | SUF+ENT | Y | OK | duplicated prefix, trailing "\|", `&#xFEFF;` |
| 4 | arts | arts.box-office | 'Jana Nayagan' box office collections day 3: Thalapathy Vijay film jumps 34.8% | OK | OK | Y | OK | |
| 5 | geopolitics | geopolitics.natural-gas-pipelines | [vi] EU miễn trừ LNG của Nga xuất sang Hàn Quốc | OK | ENT | N(vi) | OK | #63 |
| 6 | geopolitics | geopolitics.natural-gas-pipelines | [zh] 第二艘国产18万方超大型LNG船交付… | MARG | ENT | N(zh) | OK | LNG-carrier delivery = industry/business; energy-security framing arguable (same call as Part 1 row 9); #63 |
| 7 | geopolitics | geopolitics.natural-gas-pipelines, geopolitics.oil-opec | Kuwait Signs $16 Billion Pipeline Deal With Blackstone, KKR, Brookfield… | OK | OK | Y | OK | |
| 8 | business | business.inflation, geopolitics.natural-gas-pipelines | Short Interest in Amplify Samsung U.S. Natural Gas Infrastructure ETF… | OK | OK | Y | OK | programmatic finance content — thin but honestly tagged |
| 9 | ai | ai.compute-energy-demand, geopolitics.natural-gas-pipelines | Meta Exits Renewable Energy Initiative as Natural Gas Data Centers… | OK | OK | Y | OK | |
| 10 | geopolitics | geopolitics.natural-gas-pipelines | ONGC Kicks Off Landmark Deepwater Drilling in Mahanadi Basin | MARG | OK | Y | OK | corporate energy-exploration story; geopolitics via energy-security follow is arguable |
| 11 | business | business.inflation, business.interest-rates-fed | Prediction: Kevin Warsh and the FOMC Will Not Raise Interest Rates in 2026 | OK | OK | Y | OK | |
| 12 | business | business.interest-rates-fed, geopolitics.oil-opec | Every Time President Trump Talks About Iran, Oil Prices Move… | OK | OK | Y | OK | |
| 13 | business | business.interest-rates-fed | CNN Fact Check Reviews Trump's Claims at White House Correspondents' Dinner | **BAD** | OK | Y | **BAD** | political fact-check chipped business; the `business.interest-rates-fed` slug is a query false positive — nothing economic in the story |
| 14 | business | business.inflation, business.interest-rates-fed, crypto | [ko] 비트코인 ETF 이틀 연속 유출, 블랙록 IBIT서만 4억달러 넘게 빠졌다 | OK | ENT | N(ko) | OK | #63 |
| 15 | business | business.interest-rates-fed, geopolitics.oil-opec | Iran war, tariffs raise new risks for a resilient U.S. economy | OK | OK | Y | OK | |
| 16 | business | business.interest-rates-fed | Average rate on a 30-year mortgage hits the highest level in months | OK | OK | Y | OK | |
| 17 | business | business.inflation | The 'rockets and feathers' effect: Inflation eases but grocery bills do not – Daily News | OK | SUF+ENT | Y | OK | "– Daily News"; `&#x2013;`. Part 1 chipped this politics (MARG) — business is better |
| 18 | business | business.inflation | Inflation, economy cited as shoplifting rises, survey says | OK | OK | Y | OK | |
| 19 | sport | sport.soccer | [es] Los diez hechos que marcan el comienzo de la Liga BetPlay 2026-II… | OK | ENT | N(es) | OK | #63 |
| 20 | sport | sport.soccer | Premier League champions Arsenal eye Real Madrid forward Vinicius Jr | OK | OK | Y | OK | Part 1's equivalent transfer story was chipped **environment** |
| 21 | geopolitics | geopolitics.critical-minerals | Japan identifies large share of rare earths in deep-sea mud off remote island | OK | OK | Y | OK | |
| 22 | geopolitics | geopolitics.critical-minerals | Lynas says Malaysia heavy rare earth project has gone over budget | OK | OK | Y | OK | |
| 23 | geopolitics | geopolitics.critical-minerals | [de] European Lithium Aktie: 34 Prozent Crash in 30 Tagen | MARG | OK | N(de) | OK | share-price story; markets framing dominates, critical-minerals link real; #63 |
| 24 | geopolitics | geopolitics.critical-minerals | UN debate puts spotlight on critical minerals governance – Channel Africa | OK | SUF+ENT | Y | OK | "– Channel Africa" |
| 25 | geopolitics | geopolitics.oil-opec | THE CLOSING BELL: Stocks waver on Wall Street while crude oil prices fall… | **BAD** | OK | Y | OK | Wall-Street closing wrap chipped geopolitics; the oil mention is incidental. **Regression**: the same story was correctly **business** in Part 1 (row 23) |
| 26 | geopolitics | geopolitics.oil-opec | Crude Oil Trading Isn't Trading Higher Despite Iran and Ukraine Wars | OK | OK | Y | OK | |
| 27 | geopolitics | geopolitics.oil-opec | Oil Prices Dip Below $100 as Middle East Risk Weighs | OK | OK | Y | OK | |
| 28 | geopolitics | geopolitics.oil-opec | [de] Krieg in Nahost \| DIW: Ölversorgung in Europa gesichert – Kerosin nicht | OK | ENT | N(de) | OK | #63 |
| 29 | sport | sport.cricket | Sri Lanka Women Level ODI Series Against Pakistan… | OK | OK | Y | OK | |
| 30 | sport | sport.cricket.india | Ishan Kishan sets up 90-run win over Zimbabwe as India clinches T20 series | OK | OK | Y | OK | |
| 31 | sport | sport.cricket | Rain interrupts opening Test after Pak strike early against WI | OK | OK | Y | OK | |
| 32 | sport | sport.cricket | Pakistan opener Abdullah Fazal ruled out of West Indies Test series… | OK | OK | Y | OK | **was `tech` in Part 1 (row 27)** — #70 fix confirmed |
| 33 | tech | tech.launches-missions | NASA Watchdogs Warn Starship … - FINCHANNELNASA Watchdogs Raise New Concerns… | OK | **CAT** | Y | OK | two headlines concatenated with outlet name fused ("- FINCHANNELNASA"); n=1 outlet |
| 34 | tech | ai.compute-energy-demand, tech.launches-missions | [it] Musk celebra il volo di prova di Starship mentre la Nasa valuta la Luna | OK | OK | N(it) | OK | **was `environment` in Part 1 (row 30)**; #63 |
| 35 | tech | tech.launches-missions | NASA Artemis III Advances Lunar Exploration with SpaceX and Blue Origin… | OK | OK | Y | OK | n=1 outlet |
| 36 | ai | ai.compute-energy-demand | The newest bipartisan issue: not wanting an AI data center next door | OK | OK | Y | OK | |
| 37 | ai | ai.compute-energy-demand | [it] Come cambia un piccolo comune se ci metti tre data center | OK | OK | N(it) | OK | #63 |
| 38 | ai | ai.compute-energy-demand | Lefty non-profit backed protest group targeting new Pennsylvania data center… | OK | OK | Y | OK | editorialising headline, still informative |
| 39 | ai | ai.compute-energy-demand | My Visit to the Data Center | OK | OK | Y | OK | weak first-person blog title; not masthead/fragment |
| 40 | ai | ai.compute-energy-demand | [it] Via le torri della centrale, a Trino arriva il data center… | OK | OK | N(it) | OK | #63 |
| 41 | sport | sport | "Every tournament a fresh challenge": Boxer Preeti gears up to chase CWG2026 glory | OK | OK | Y | OK | |
| 42 | sport | sport.cricket.india | Srikkanth wants Siraj to be picked up for 2027WC… | OK | OK | Y | OK | |
| 43 | arts | arts.bollywood | Abhishek Bachchan and Aishwarya Rai spotted clicking pictures with fan… | OK | ENT | Y | OK | **was `tech` in Part 1 (row 36)**; `&#xA0;` |
| 44 | arts | arts.bollywood | Bollywood Celebrates Dharmendra Pradhan's Resignation Amid NEET Protest Victory | MARG | OK | Y | OK | underlying event is a ministerial resignation (politics); headline subject is the Bollywood reaction |
| 45 | arts | arts.bollywood | Salman Khan drops cryptic message with gym photos… : Bollywood News | OK | SUF | Y | OK | ": Bollywood News" |
| 46 | arts | arts.bollywood | Amid Silence On CJP Protest, Shah Rukh Khan Enjoys Family Vacation In London | OK | OK | Y | OK | |
| 47 | ai | ai.alignment-research | OpenAI eyeing ChatGPT integrations for smart glasses, wearables: Greg Brockman | OK | OK | Y | OK | category right; `alignment-research` leaf is a stretch for a product story |
| 48 | ai | ai.alignment-research | OpenAI quietly signs letter from Nvidia, Microsoft, and Meta warning… | OK | ENT | Y | OK | `&#x2014;` |
| 49 | ai | ai.alignment-research | [zh] 中国大模型"撕裂"硅谷！走访Kimi的美国研究员… | OK | ENT | N(zh) | OK | #63 |
| 50 | ai | ai.alignment-research | OpenAI's AI Escaped Its Lab and Hacked Another Company | OK | OK | Y | OK | |
| 51 | ai | ai.alignment-research | [zh] Anthropic 57天密集更新！…-人工智能-ITBear科技资讯 | OK | SUF+ENT | N(zh) | OK | #63 |
| 52 | ai | ai.alignment-research | US lawmakers propose AI 'kill switch' after OpenAI test | OK | OK | Y | OK | |
| 53 | sport | sport.fifa, sport.fifa-world-cup | Cabo Verde goalie Vozinha headed to Chile post FIFA World Cup 2026 stardom | OK | OK | Y | OK | |
| 54 | sport | sport.fifa-world-cup | 'He won the World Cup!' Dubliner Pico Lopes welcomed home to local club | OK | OK | Y | OK | |
| 55 | business | crypto | Bitcoin nation: Trump bets America's future on crypto | OK | OK | Y | OK | |
| 56 | business | crypto | Strategy Debuts Net Bitcoin Per Share for Common Investors | OK | OK | Y | OK | |
| 57 | business | crypto | New to The Street to Broadcast Nationwide on Bloomberg Television Tonight… | OK | OK | Y | OK | tag honest, but the item is a **press release / promo**, not news — notability, see below |
| 58 | arts | entertainment | Maya Jama 'set to sign on for third series of hit Netflix show'… | OK | OK | Y | OK | Part 1 chipped streaming items **geopolitics** |
| 59 | arts | entertainment | Weekend Watchlist: What's new in theaters, on streaming | OK | OK | Y | OK | weak roundup teaser, not masthead/fragment |
| 60 | sport | sport.fifa | 2026 World Cup: IOC speaks on investigating FIFA president, Infantino | OK | OK | Y | OK | |
| 61 | sport | sport.fifa | [es] Se transparentó uso del Castillo de Chapultepec para la cena de la FIFA | OK | ENT | N(es) | OK | #63 |
| 62 | sport | sport.fifa | [es] FIFA publica ranking de rendimiento de los jugadores en el Mundial… | OK | ENT | N(es) | OK | #63 |
| 63 | sport | sport.fifa | [id] Spanduk Kepulauan Malvinas dalam Perayaan Argentina… Picu Penyelidikan FIFA | OK | OK | N(id) | OK | #63 |
| 64 | ai | ai.data-center-buildout | CoreWeave Stock Fell 11.4% on Friday. The Sell-Off Is About What It's Spending… | MARG | OK | Y | OK | equity/markets framing dominates; AI-capex link real |
| 65 | business | business.gdp-growth | [zh] 河南GDP领跑全国5%增速 高技术制造业迎重大投资机遇- CFi.CN 中财网 | OK | SUF+ENT | N(zh) | OK | "- CFi.CN 中财网"; #63 |

## Batch-wide arithmetic (all sums checked against 65)

**Category counts:** ai 14 · arts 8 · business 14 · geopolitics 12 · sport 14 · tech 3 —
total 65. `environment` 0, `politics` 0, `wildcard` 0, `markets` 0.

**Tag honesty:** OK 58 · MARG 5 (rows 6, 10, 23, 44, 64) · BAD 2 (rows 13, 25).

- Strict rate: **58/65 = 89.2%** (needed ≥61/65 = 93.3%) → **FAIL by 3 rows**
- Lenient rate (MARG honest): **63/65 = 96.9%** → **PASS**
- Non-English rows (#63, reported not excused): **15** (rows 5, 6, 14, 19, 23, 28, 34, 37, 40,
  49, 51, 61, 62, 63, 65 — it×3, zh×4, es×3, de×2, vi, ko, id). English share 50/65 = 76.9%
- #63-adjusted (English-only, 50 rows): OK 45 · MARG 3 · BAD 2 → strict **45/50 = 90.0%**
  (gate 47) → FAIL; lenient 48/50 = 96.0% → PASS

**Headlines:** pure masthead or fragment **0/65** (literal criterion PASS). Residuals (#71):
unescaped HTML entities **15** rows (3, 5, 6, 14, 17, 19, 24, 28, 43, 48, 49, 51, 61, 62, 65);
trailing outlet-suffix junk **8** rows (2, 3, 17, 24, 33, 45, 51, 65); concatenated double-title
with fused outlet name **1** row (33) — a new #71 sub-class, worse than plain suffix junk.

**Interest-match plausibility:** untagged **0** rows (was 17). Of 65 tagged rows, ≥1 plausible
slug on **64**; implausible on **1** (row 13). All **22** distinct matched slugs ⊆ ash's 26
followed interests, and `shortlist_matched_slug_outside_followed_set` fired **0** times.

**Followed-set context (why environment/politics are 0):** ash follows 26 interests spanning
ai×6, business×5, sport×6, arts×3, geopolitics×3, tech×1, plus roots `crypto` and
`entertainment`. There is **no environment and no politics interest in that set**, so the
taxonomy cannot honestly produce those categories for this profile. Part 1's environment 2 /
politics 4 came from the phantom-root bug, and Part 1 already recorded honest environment
stories = 0.

**Latent drift spotted (not a this-run failure):** two followed interests carry legacy
`interest_segment_slug` values — `crypto` → `markets` and `entertainment` → `wildcard` — the
exact two segments #47 requires to be zero. The resolver correctly maps their stories to
`business` / `arts`, so the artifact is clean; but any code path reading
`interest_segment_slug` directly would emit `markets` / `wildcard` chips. Same family as the
2026-07-07 stale-`_VALID_SEGMENT_SLUGS` RCA. Worth a hygiene slice.

## Residual failure classes (ranked)

1. **Query-precision false positives (2 rows, the whole strict-gate gap).** Row 13
   (`business.interest-rates-fed` matched a political fact-check) and row 25 (a Wall-Street
   closing wrap pulled in by `geopolitics.oil-opec` and then chipped geopolitics). Both are
   *ingestion-query* precision, not the #70 scrambling bug. Row 25 is a genuine regression
   against Part 1. Adjacent to #69 (query contract) — not currently covered by a filed issue
   for the category-resolution half.
2. **#63 non-English — 15/65 (23%).** Unchanged gap; English-only adjustment does *not* rescue
   the strict rate (45/50 = 90.0%, still below 93.3%).
3. **#71 headline hygiene — 15 entity rows + 8 suffix rows + 1 concatenated title.** Row 33's
   fused double-title is a new sub-class worth adding to that issue.
4. **Notability floor.** Rows 8, 57 and the n=1 rows (33, 35) are thin/promotional. Matches the
   `#47`-checklist notability-recalibration item recorded in e55faa6.

## Delta: pre-#70 → post-#70

| Metric | Pre-#70 (55 rows) | Post-#70 (65 rows) | Delta |
|---|---|---|---|
| Strict honest tags | 34/55 = **61.8%** | 58/65 = **89.2%** | **+27.4 pp** |
| Lenient honest tags | 41/55 = 74.5% | 63/65 = **96.9%** | **+22.4 pp** |
| Clearly-wrong (BAD) tags | **14** | **2** | −12 |
| Untagged rows | 17 | **0** | −17 |
| Matched slugs that are followed leaves | 0 | **22/22** | fixed |
| English-only strict | 26/44 = 59.1% | 45/50 = **90.0%** | +30.9 pp |
| Non-English share | 11/55 = 20% | 15/65 = 23% | +3 pp (#63 untouched) |

**Read:** #70 did what it was filed to do — the scrambled-chip class is gone (14 BAD → 2, and
neither survivor is a theme-root scramble). The remaining 3-row gap to the gate is a different
defect class (query precision + one category-resolution regression), plus the untouched #63
non-English gap.

## Status of #47's literal acceptance criteria

| AC | Status this run |
|---|---|
| ≥28/30 honest tags in a fresh **30-row briefing** | **NOT run as written** — no briefing exists (no story text produced, per founder directive). Judged against the 65-row shortlist by rate: **FAIL strict**, PASS lenient |
| Zero masthead/fragment headlines in the 30 | **PASS on the shortlist** (0/65); the literal "in the 30" is out of scope |
| Chip + headline on separate lines, **on-device** | **OUT OF SCOPE** — needs a producing run + device build. Explicitly NOT claimed |
| Non-zero ai/business/environment/politics/arts; zero wildcard/markets | **PARTIAL FAIL** — ai/business/arts non-zero, wildcard/markets zero; environment/politics zero and structurally unreachable for this profile |
| Py/TS twin green | **PASS** (scoped; 1 inherited failure disclosed) |
| Error/boundary honesty | **PASS** |

## Next step

The remaining gate gap is 3 rows and two of them are one defect class. Recommended order:
fix the query-precision / category-resolution residual (rows 13, 25) → #63 English filter (the
single largest quality lever left, 15 rows) → #71 headline hygiene → then a producing run and
the on-device check under explicit founder go.
