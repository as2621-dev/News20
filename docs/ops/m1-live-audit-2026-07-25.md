# M1 live audit (issue #47) — 2026-07-25: batch ran, audit done, **FAIL on tag honesty (34/55 strict)**

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
