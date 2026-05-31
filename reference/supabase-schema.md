# Supabase Schema — blip (repo "News20")

**Why this doc exists:** The single authoritative Postgres schema that can fully populate **every field the design prototype uses**. It is the contract between the design handoff (`prototype/News20 Prototype/data.js` + `app.js`), the API shapes (`reference/api-contracts.md`), and the Python worker that ports TLDW (`reference/reuse-map.md`). Verbose, intention-revealing column names per `reference/conventions.md` — never bare `id`/`name`.

**Scope note — four pivots baked in:**
1. **Brand** is `blip` (was News20). Repo/codename stays `News20`.
2. **Audio-first reel.** The reel is rendered **live, client-side** from `(digest audio + word-timed caption JSON + an ambient drifting poster wash)` — there is **no pre-rendered MP4**. `caption_sentences.word_tokens` is therefore the load-bearing table, not a video file. (See `prototype/.../ui-design-decisions.md` §0.)
3. **Chip-based hierarchical onboarding.** A tappable **category → subcategory → sub-subcategory** interest picker builds the profile; the checkbox grid is gone. This is why `interests` is a **hierarchical taxonomy**. *(Voice-agent onboarding was dropped 2026-05-30 — onboarding is chip-only; in-news Voice mode survives, see §2 row 7. `onboarding_conversations` was therefore never created; `user_interest_traits` + the `'voice'` enum value shipped in migration 0003 (applied) and are retained in the live DB but unused/deprecated.)*
4. **Auth** is **email-only passwordless magic-link** (Supabase email OTP). Sign-in-with-Apple removed. `users` maps 1:1 to Supabase `auth.users`.

**When to update:** whenever a stored entity or a prototype field shape changes. Keep the TS interfaces in `src/types/`, the Pydantic models, and this DDL in sync (`reference/api-contracts.md`).

> **Onboarding build target (the real spec, ahead of the prototype code):** the prototype still ships the older *scripted* `VP_TURNS` voice flow. The schema below is built for the **final** target the owner asked for: **chip-based onboarding** with on-screen **tappable category chips** backed by a **dynamic hierarchical taxonomy** (category → subcategory → sub-subcategory) and per-interest niche-down via chip drill-down (e.g. Sport → which team). That target is the reason `interests` is a self-referencing tree. *(The voice-agent interview was dropped 2026-05-30; onboarding is chip-only.)*

---

## 0. Conventions used in this schema

- **Naming:** every PK is `<table_singular>_id` (e.g. `story_id`), every column carries its entity prefix (`coverage_left_count`, `digest_audio_url`). No bare `id`/`name`/`url`.
- **Keys:** UUID PKs via `gen_random_uuid()` for user-facing rows; short **text** PKs (`'s1'`, `'geopolitics'`, `'ALEX'`) where the prototype already uses a stable human-readable slug — preserving them keeps the design payload portable and the seed data legible.
- **Time:** `timestamptz` everywhere, suffix `_at` / `_utc`. Defaults `now()`.
- **Enums:** `CREATE TYPE ... AS ENUM` for closed sets (bias lean, segment slug, signal event type, profile source).
- **JSONB** only where the shape is genuinely variable or token-array-shaped (`word_tokens`, transcripts, extracted profiles). Everything addressable by a query is a real column.
- **RLS:** content tables are public-read; user tables are per-`auth.uid()`. See §6.

---

## 1. Enums

```sql
-- Bias lean for outlets and per-source rows (AllSides / Ad Fontes model).
CREATE TYPE bias_lean AS ENUM ('left', 'center', 'right');

-- Fixed top-level editorial segments. Mirrors data.js SEGMENTS keys exactly.
CREATE TYPE segment_slug AS ENUM ('geopolitics', 'markets', 'tech', 'sport', 'wildcard');

-- Where an interest weight came from (typed chip onboarding or implicit engagement signal).
-- NOTE: 'voice' is DEPRECATED/unused — voice-agent onboarding was dropped 2026-05-30. It is
-- retained because it shipped in migration 0003 (applied) and Postgres can't cleanly drop an enum value.
CREATE TYPE interest_profile_source AS ENUM ('voice', 'typed', 'signal');

-- Implicit engagement events feeding category prioritization (reuse: TLDW player_signals).
CREATE TYPE player_signal_event AS ENUM (
  'play', 'complete', 'open_detail', 'ask', 'voice', 'save', 'follow', 'skip'
);

-- Which anchor voice spoke a caption sentence. Mirrors data.js anchors[] + app.js identity colours.
CREATE TYPE anchor_speaker AS ENUM ('ALEX', 'JORDAN');
```

---

## 2. Content tables (public-read; written by the ingestion/generation worker)

### `segments`
Purpose: the five fixed editorial segments with their accent colour.
Maps: `data.js SEGMENTS` — `{ geopolitics: { label, accent }, markets, tech, sport, wildcard }`.

```sql
CREATE TABLE segments (
  segment_slug        segment_slug PRIMARY KEY,                  -- 'geopolitics' ...
  segment_label       text         NOT NULL,                     -- "Geopolitics", "Tech & Science"
  segment_accent_hex  text         NOT NULL,                     -- "#EF4444" — the per-story --accent
  segment_sort_order  smallint     NOT NULL DEFAULT 0
);
```

### `outlets`
Purpose: static AllSides/Ad Fontes outlet→bias lookup; the source of truth for every coverage number.
Maps: indirectly powers `story.trust.coverage.{left,center,right}` and `story.citations` (e.g. "CNN", "Reuters", "ESPN"). Per `reference/integrations.md` this is a **one-time static table, not a per-story API call**.

```sql
CREATE TABLE outlets (
  outlet_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  outlet_name            text      NOT NULL UNIQUE,               -- "CNN", "Reuters", "ESPN"
  outlet_bias_lean       bias_lean NOT NULL,                     -- AllSides L/C/R classification
  outlet_bias_score      numeric,                                -- optional Ad Fontes numeric (-42..+42)
  outlet_reliability     numeric,                                -- optional Ad Fontes reliability axis
  outlet_homepage_url    text,
  outlet_created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_outlets_bias_lean ON outlets (outlet_bias_lean);
```

### `stories`
Purpose: one real-world story, clustered across many outlets — the spine of the reel.
Maps: `data.js story` top-level fields — `id`, `segment`, `image`, `headline`, `outlet`, `time`, `dek`, plus `keyFigure.{value,label}` and the `trust` scalars (`outlet_count`, `blindspot`). Aligns with `Story` in `api-contracts.md`.

```sql
CREATE TABLE stories (
  story_id                   text PRIMARY KEY,                   -- 's1'..'s5' in prototype; slug/uuid in prod
  story_segment_slug         segment_slug NOT NULL REFERENCES segments (segment_slug),
  story_headline             text NOT NULL,                      -- story.headline
  story_dek                  text NOT NULL,                      -- story.dek (one-line subhead)
  story_primary_outlet_id    uuid REFERENCES outlets (outlet_id),-- story.outlet, resolved to outlets
  story_primary_outlet_name  text,                               -- denormalized story.outlet ("CNN") for fast render
  story_ambient_poster_url   text,                               -- story.image (assets/s1.png) → ambient wash source
  story_first_reported_utc   timestamptz NOT NULL DEFAULT now(), -- story.time anchored to a date in prod
  story_last_updated_utc     timestamptz NOT NULL DEFAULT now(),
  story_published_label      text,                               -- story.time display string ("08:10")
  -- key figure card (Detail view)
  story_key_figure_value     text,                               -- keyFigure.value ("~20%", "$81.6B")
  story_key_figure_label     text,                               -- keyFigure.label ("of global oil transits Hormuz")
  -- denormalized trust summary (detail authority strip; full breakdown in story_trust)
  story_outlet_count         integer NOT NULL DEFAULT 0,         -- trust.outlet_count (19, 21, ...)
  story_blindspot_lean       bias_lean,                          -- trust.blindspot ('right' | NULL)
  story_blindspot_flag       boolean GENERATED ALWAYS AS (story_blindspot_lean IS NOT NULL) STORED,
  story_created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_stories_segment        ON stories (story_segment_slug);
CREATE INDEX idx_stories_last_updated   ON stories (story_last_updated_utc DESC);
```

### `digests`
Purpose: the generated audio digest + its playback metadata. **Audio-first: this is audio + a caption track, NOT an MP4.**
Maps: there is no single `data.js` field — the prototype fakes a `13000ms` `S.duration` and renders captions on a timer. In production the digest carries the real TTS audio and total duration that drives `caption_sentences` word timings. Supersedes `Digest.digest_mp4_url` in `api-contracts.md` (kept nullable for any legacy/poster-pipeline render).

```sql
CREATE TABLE digests (
  digest_id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  digest_story_id            text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  digest_audio_url           text NOT NULL,                      -- Supabase storage: anchor-duo TTS narration
  digest_duration_ms         integer NOT NULL,                   -- real total (proto: S.duration = 13000)
  digest_ambient_poster_url  text,                               -- graded poster → drifting duotone wash
  digest_caption_track_url   text,                               -- optional: forced-alignment JSON blob mirror
  digest_legacy_mp4_url      text,                               -- nullable; only if a video render also exists
  digest_is_current          boolean NOT NULL DEFAULT true,      -- one current digest per story
  digest_generated_utc       timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_digests_current_per_story
  ON digests (digest_story_id) WHERE digest_is_current;
```

### `caption_sentences`  ← **the karaoke hero table**
Purpose: the word-timed karaoke caption track — the single most important table in the audio-first design.
Maps: `data.js cap()` output. Each `cap("…[keyword]…")` produces `{ words: [{ t, hl }] }`; `app.js` alternates `anchor_speaker` per sentence (`st.anchors[si % 2]`) and renders exactly one `.hl` keyword per sentence.

**Word-sync rationale.** The prototype only has `{t, hl}` (text + is-it-the-yellow-keyword) and *fakes* timing by distributing `S.duration` proportionally to word count (`app.js sentenceBounds()` / `paintCaption()`). Production replaces that fake with **real forced alignment** (TLDW `agents/pipeline/stages/forced_alignment.py`, **PORT** per `reuse-map.md`): every word token carries `start_ms`/`end_ms` relative to `digest_audio_url`, so the karaoke "dim → current word white → one `#FACC15` keyword" lights in lockstep with the spoken audio. We store the token array as JSONB because it is naturally a variable-length, per-sentence array consumed whole by the client renderer; the scalar columns (`sentence_index`, `anchor_speaker`, `highlight_keyword`, `sentence_text`) stay first-class so the backend can query/validate without parsing JSON.

```sql
CREATE TABLE caption_sentences (
  caption_sentence_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  caption_digest_id      uuid NOT NULL REFERENCES digests (digest_id) ON DELETE CASCADE,
  caption_story_id       text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  sentence_index         smallint NOT NULL,                      -- 0-based order within the digest
  anchor_speaker         anchor_speaker NOT NULL,                -- who speaks it (st.anchors[si % 2])
  sentence_text          text NOT NULL,                          -- full plaintext sentence (search/QA/RAG)
  highlight_keyword      text NOT NULL,                          -- the single #FACC15 word per sentence
  sentence_start_ms      integer NOT NULL,                       -- forced-alignment start vs digest audio
  sentence_end_ms        integer NOT NULL,                       -- forced-alignment end
  -- karaoke tokens: [{ "word_text": "target", "is_highlight": true, "start_ms": 1840, "end_ms": 2210 }, ...]
  -- shape extends cap()'s {t, hl}: t→word_text, hl→is_highlight, plus real per-word start_ms/end_ms.
  word_tokens            jsonb NOT NULL,
  CONSTRAINT uq_caption_sentence_order UNIQUE (caption_digest_id, sentence_index)
);

CREATE INDEX idx_caption_sentences_digest ON caption_sentences (caption_digest_id, sentence_index);
```

### `detail_chunks`
Purpose: the chunked readable body text in the swipe-right Detail view (Playfair body, "<100s read").
Maps: `data.js story.detail_chunks` (array of paragraph strings). Aligns with `StoryDetail.readable_text_chunks`.

```sql
CREATE TABLE detail_chunks (
  detail_chunk_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  detail_story_id     text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  chunk_index         smallint NOT NULL,                         -- 0-based paragraph order
  chunk_text          text NOT NULL,                             -- one detail_chunks[] paragraph
  CONSTRAINT uq_detail_chunk_order UNIQUE (detail_story_id, chunk_index)
);

CREATE INDEX idx_detail_chunks_story ON detail_chunks (detail_story_id, chunk_index);
```

### `story_trust`
Purpose: the per-story trust summary backing the Detail "COVERAGE" strip.
Maps: `data.js story.trust` — `coverage.{left,center,right}`, `outlet_count`, `blindspot`, `opposing_view`. (Timeline is normalized into `story_timeline`.) Aligns with `BiasBreakdown` + `Story.blindspot_flag` in `api-contracts.md`.

```sql
CREATE TABLE story_trust (
  story_trust_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trust_story_id          text NOT NULL UNIQUE REFERENCES stories (story_id) ON DELETE CASCADE,
  coverage_left_count     integer NOT NULL DEFAULT 0,            -- trust.coverage.left
  coverage_center_count   integer NOT NULL DEFAULT 0,            -- trust.coverage.center
  coverage_right_count    integer NOT NULL DEFAULT 0,            -- trust.coverage.right
  coverage_outlet_count   integer NOT NULL DEFAULT 0,            -- trust.outlet_count ("COVERED BY 19 OUTLETS")
  blindspot_lean          bias_lean,                             -- trust.blindspot ('right' | NULL)
  opposing_view_text      text                                   -- trust.opposing_view (the light card quote)
);
```
> **Blindspot rule (`reuse-map.md` bias layer):** `blindspot_lean` is set when one side is materially under-covered (>70% of coverage on the other sides). The prototype hardcodes it; production derives it from the three counts at ingestion time.

### `story_timeline`
Purpose: the "HOW IT DEVELOPED" expandable drawer events; also the freshness source for the follows "what's new" query.
Maps: `data.js story.trust.timeline[]` — `{ when, what }`. The latest event per followed story powers the `● NEW` badge in `app.js openFollowing()`.

```sql
CREATE TABLE story_timeline (
  story_timeline_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  timeline_story_id     text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  timeline_event_index  smallint NOT NULL,                       -- order within the story
  timeline_when_label   text NOT NULL,                           -- timeline[].when ("08:10", "Mon", "1993")
  timeline_event_at     timestamptz NOT NULL DEFAULT now(),      -- real sortable timestamp (drives "what's new")
  timeline_what_text    text NOT NULL,                           -- timeline[].what (the development sentence)
  CONSTRAINT uq_story_timeline_order UNIQUE (timeline_story_id, timeline_event_index)
);

CREATE INDEX idx_story_timeline_story_at ON story_timeline (timeline_story_id, timeline_event_at DESC);
```

### `story_sources`
Purpose: the source outlets backing a story, sortable by bias + recency (Detail "sources" + citation chips).
Maps: `data.js story.citations[]` (e.g. `["CNN", "Reuters"]`) joined to `outlets` for the bias lean. Aligns with `StorySource` in `api-contracts.md`.

```sql
CREATE TABLE story_sources (
  story_source_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_story_id        text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  source_outlet_id       uuid REFERENCES outlets (outlet_id),
  source_outlet_name     text NOT NULL,                          -- citations[] entry, denormalized
  source_bias_lean       bias_lean,                              -- resolved from outlets (sort key)
  source_article_url     text,                                   -- canonical article link (for RAG + citation)
  source_published_utc   timestamptz,
  source_is_citation     boolean NOT NULL DEFAULT true,          -- shown as a Q&A citation chip
  CONSTRAINT uq_story_source UNIQUE (source_story_id, source_outlet_name)
);

CREATE INDEX idx_story_sources_story ON story_sources (source_story_id);
```

### `suggested_questions`
Purpose: the tappable suggested-question chips in Detail Q&A and Voice mode.
Maps: `data.js story.suggested_questions[]` (e.g. "What led to this?", "Why does Hormuz matter?").

```sql
CREATE TABLE suggested_questions (
  suggested_question_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_story_id       text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  question_index          smallint NOT NULL,                     -- chip display order
  question_text           text NOT NULL,                         -- suggested_questions[] entry
  CONSTRAINT uq_suggested_question_order UNIQUE (question_story_id, question_index)
);
```

### `story_qa`
Purpose: per-story question→grounded-answer pairs. **CANNED in the prototype; RAG-retrieved + verified in production.**
Maps: `data.js story.answers` — an object keyed by the suggested question text → grounded answer string.

> **RAG vs canned (Decision #5, `reuse-map.md` interrogation layer + `ui-design-decisions.md` §7).** In the prototype, `app.js resolveAnswer()` looks up `story.answers[q]` for an exact suggested-question match, else fuzzy-matches against `story.topics`, else returns the **mandatory grounded-refusal**. In production this table is **not the answer source** — answers come from the RAG retriever (TLDW `agents/rag/*`, **PORT**) over `story_sources` text, then pass the `verification` stage (**PORT**) before display. We keep `story_qa` for (a) seed/demo parity, (b) editorially curated canned answers, and (c) caching verified answers. **The on-topic→grounded / off-topic→refusal contract (`answer_is_grounded`) must be preserved exactly** — it is how users learn to trust the system.

```sql
CREATE TABLE story_qa (
  story_qa_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  qa_story_id           text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  qa_question_text      text NOT NULL,                           -- key of story.answers
  qa_answer_text        text NOT NULL,                           -- value of story.answers (grounded answer)
  qa_is_grounded        boolean NOT NULL DEFAULT true,           -- false → render the refusal state
  qa_source_kind        text NOT NULL DEFAULT 'canned',          -- 'canned' | 'rag_cached'
  qa_citation_outlet_names text[] NOT NULL DEFAULT '{}',         -- citation chips for this answer
  qa_created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_story_qa UNIQUE (qa_story_id, qa_question_text)
);

CREATE INDEX idx_story_qa_story ON story_qa (qa_story_id);
```

### `story_topics`
Purpose: the curated topic keyword list that gates whether free-text Q&A is on-topic (grounded) vs off-topic (refusal). **CANNED in the prototype; in production this is a fallback gate behind the RAG retriever.**
Maps: `data.js story.topics[]` (e.g. `["iran","strike","hormuz",...]`). `app.js resolveAnswer()` uses `story.topics.some(t => ql.includes(t))` to decide grounded vs refusal.

```sql
CREATE TABLE story_topics (
  story_topic_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_story_id     text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  topic_keyword      text NOT NULL,                              -- one entry of story.topics[]
  CONSTRAINT uq_story_topic UNIQUE (topic_story_id, topic_keyword)
);

CREATE INDEX idx_story_topics_story ON story_topics (topic_story_id);
```

### `story_interests`  ← **the interest-keyed fan-out join (M1 re-scope, migration 0003)**
Purpose: the M:N link from a story to the interest nodes it serves — the mechanism that distributes one deduped story to every user whose interest matches. A story is tagged to its matched node **and all ancestors** (Arsenal → Soccer → Sport), so a broad follower catches a niche story for free.
Maps: produced at ingest by `agents/ingestion/ancestor_tagging.py` (Phase 1d SP1); read by the per-user scorer (`reference/ranking-spec.md` §1–2). `story_interest_match_depth` (0 leaf / 1 parent / 2 grandparent) feeds the `DepthMatch` score term.

```sql
CREATE TABLE story_interests (
  story_interest_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  story_interest_story_id    text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  story_interest_interest_id uuid NOT NULL REFERENCES interests (interest_id) ON DELETE CASCADE,
  story_interest_match_depth smallint NOT NULL,                 -- 0 = leaf-matched, 1 = parent, 2 = grandparent
  story_interest_relevance   numeric,                           -- optional per-(story,interest) relevance from ingestion
  story_interest_created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_story_interest UNIQUE (story_interest_story_id, story_interest_interest_id)
);

CREATE INDEX idx_story_interests_interest ON story_interests (story_interest_interest_id);
CREATE INDEX idx_story_interests_story    ON story_interests (story_interest_story_id);
```

### `anchors`
Purpose: the two AI anchors with their Gemini TTS voice id and fixed identity colour.
Maps: `data.js story.anchors[]` (`"ALEX"`, `"JORDAN"`) + `app.js` identity colours `{ ALEX: "#6C8CFF", JORDAN: "#C792EA" }`. Voice ids per `reuse-map.md` (ALEX→`Leda`, JORDAN→`Sadaltager`).

```sql
CREATE TABLE anchors (
  anchor_id             anchor_speaker PRIMARY KEY,              -- 'ALEX' | 'JORDAN'
  anchor_display_name   text NOT NULL,                          -- "Alex", "Jordan"
  gemini_voice_id       text NOT NULL,                          -- 'Leda' (ALEX), 'Sadaltager' (JORDAN)
  identity_color_hex    text NOT NULL,                          -- '#6C8CFF' (ALEX), '#C792EA' (JORDAN)
  anchor_sort_order     smallint NOT NULL DEFAULT 0
);
```

---

## 3. Taxonomy & user tables

> **M1 re-scope (2026-05-30) — migration `0003` (applied).** Personalization moved from M3 into M1 (`plans/phase-1e-auth-onboarding-interest-profile.md`). Migration `0003` applies the M1 subset of this section — `users` (+ `handle_new_user()` trigger), `interests` (with the new `interest_search_query` / `interest_kind` columns), `user_interest_profile` (with `profile_is_strict`), `user_interest_traits`, `player_signals` — plus the two new pipeline tables `story_interests` (§2) and `daily_feeds` (below). **Deferred to their feature phases:** `follows` → M3 Phase 3d; `saves` / `play_sessions` → M3/M4. Onboarding is **chip-only** (`profile_source='typed'`). *(Voice-agent onboarding was dropped 2026-05-30, so `onboarding_conversations` was never created. `user_interest_traits` + the `'voice'` enum value shipped in 0003 (applied) and are retained in the DB but unused/deprecated — not un-migrated, per the owner's keep-DB-as-is decision.)* See `reference/ranking-spec.md` for how these tables are scored/allocated.

### `interests`  ← **hierarchical self-referencing taxonomy**
Purpose: the dynamic category → subcategory → sub-subcategory tree that backs the tappable onboarding chips and niche-down follow-ups (e.g. Sport → Soccer → Premier League).
Maps: replaces the prototype's flat `INTERESTS`/`VP_LABEL` lists in `app.js`. Top-level rows correspond to segment slugs; deeper rows are the niche-down targets the voice agent drills into.

**Why hierarchical.** The build target is *dynamic niche-down*: when the user says "Sport", blip asks "which team?" and surfaces tappable child chips. A flat list cannot express "Premier League is under Soccer is under Sport". A self-FK tree (`parent_interest_id`) with `depth_level` lets the voice agent walk any depth, and lets the chip UI lazy-load children of a tapped chip. `segment_id` links a top-level interest to its reel segment so an interest weight can prioritize segments at ranking time.

```sql
CREATE TABLE interests (
  interest_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_interest_id    uuid REFERENCES interests (interest_id) ON DELETE CASCADE,  -- NULL at top level
  interest_slug         text NOT NULL UNIQUE,                   -- 'sport', 'sport.soccer', 'sport.soccer.epl'
  interest_label        text NOT NULL,                          -- "Sport", "Soccer", "Premier League"
  depth_level           smallint NOT NULL DEFAULT 0,            -- 0 = category, 1 = subcategory, 2 = sub-sub
  interest_segment_slug segment_slug REFERENCES segments (segment_slug),  -- set on depth-0 rows
  interest_search_query text,                              -- M1 (0003): news query the daily pipeline ingests on (reference/ranking-spec.md §2)
  interest_kind         text NOT NULL DEFAULT 'taxonomy',  -- M1 (0003): 'taxonomy' | 'custom' (free-text onboarding interest)
  interest_sort_order   smallint NOT NULL DEFAULT 0,
  interest_is_active    boolean NOT NULL DEFAULT true,          -- taxonomy is dynamic; soft-disable nodes
  interest_created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_interest_depth CHECK (
    (depth_level = 0 AND parent_interest_id IS NULL) OR
    (depth_level > 0 AND parent_interest_id IS NOT NULL)
  )
);

CREATE INDEX idx_interests_parent  ON interests (parent_interest_id);
CREATE INDEX idx_interests_segment ON interests (interest_segment_slug);
CREATE INDEX idx_interests_depth   ON interests (depth_level);
```

#### Seed example — two 3-level chains

```sql
-- Chain A: Sport → Soccer → Premier League
INSERT INTO interests (interest_id, parent_interest_id, interest_slug, interest_label, depth_level, interest_segment_slug, interest_sort_order)
VALUES
  ('00000000-0000-0000-0000-0000000000a0', NULL,
     'sport', 'Sport', 0, 'sport', 10);
INSERT INTO interests (interest_id, parent_interest_id, interest_slug, interest_label, depth_level, interest_segment_slug, interest_sort_order)
VALUES
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000a0',
     'sport.soccer', 'Soccer', 1, NULL, 10);
INSERT INTO interests (interest_id, parent_interest_id, interest_slug, interest_label, depth_level, interest_segment_slug, interest_sort_order)
VALUES
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000a1',
     'sport.soccer.epl', 'Premier League', 2, NULL, 10);

-- Chain B: Markets → Equities → Semiconductors
INSERT INTO interests (interest_id, parent_interest_id, interest_slug, interest_label, depth_level, interest_segment_slug, interest_sort_order)
VALUES
  ('00000000-0000-0000-0000-0000000000b0', NULL,
     'markets', 'Markets', 0, 'markets', 20);
INSERT INTO interests (interest_id, parent_interest_id, interest_slug, interest_label, depth_level, interest_segment_slug, interest_sort_order)
VALUES
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000b0',
     'markets.equities', 'Equities', 1, NULL, 10);
INSERT INTO interests (interest_id, parent_interest_id, interest_slug, interest_label, depth_level, interest_segment_slug, interest_sort_order)
VALUES
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000b1',
     'markets.equities.semis', 'Semiconductors', 2, NULL, 10);
```

```sql
-- Walk the full tree with a recursive CTE (drives lazy chip expansion + agent niche-down):
WITH RECURSIVE interest_tree AS (
  SELECT interest_id, parent_interest_id, interest_label, depth_level, interest_label::text AS interest_path
  FROM interests WHERE parent_interest_id IS NULL
  UNION ALL
  SELECT child.interest_id, child.parent_interest_id, child.interest_label, child.depth_level,
         parent.interest_path || ' → ' || child.interest_label
  FROM interests child
  JOIN interest_tree parent ON child.parent_interest_id = parent.interest_id
)
SELECT interest_path FROM interest_tree ORDER BY interest_path;
-- → "Sport → Soccer → Premier League", "Markets → Equities → Semiconductors", ...
```

### `users`
Purpose: app-level user profile, 1:1 with Supabase `auth.users` (email-only magic-link).
Maps: the email captured in `app.js onbStep()` step 3 ("we'll email you a sign-in link — no password") + the profile sheet's display fields ("Commuter", "12-DAY STREAK").

> **Auth mapping (email-only pivot).** `auth.users` is managed by Supabase (email OTP / magic-link). This `users` row is the public app profile; `user_id` equals `auth.uid()`. Create it from a Supabase trigger on `auth.users` insert, or on first authenticated request. No password column — there are no passwords.

```sql
CREATE TABLE users (
  user_id                uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  user_email             text NOT NULL UNIQUE,                  -- mirror of auth email (magic-link target)
  user_display_label     text NOT NULL DEFAULT 'Commuter',      -- profile sheet name ("Commuter")
  user_onboarded_at      timestamptz,                           -- set when onboarding completes
  user_streak_day_count  integer NOT NULL DEFAULT 0,            -- "12-DAY STREAK"
  user_created_at        timestamptz NOT NULL DEFAULT now(),
  user_last_active_at    timestamptz NOT NULL DEFAULT now()
);
```

### `user_interest_profile`
Purpose: the per-user weighted interest graph the chip onboarding builds; drives reel ranking.
Maps: the chip interest picker (replacing `app.js voiceProfileStep()`) — selected interests become weighted picks; `profile_source` records whether it came from a typed chip pick or an implicit engagement signal (`'voice'` is unused — voice onboarding dropped).

```sql
CREATE TABLE user_interest_profile (
  user_interest_profile_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_user_id           uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  profile_interest_id       uuid NOT NULL REFERENCES interests (interest_id) ON DELETE CASCADE,
  profile_weight            numeric NOT NULL DEFAULT 1.0,        -- ranking weight (signals nudge over time)
  profile_source            interest_profile_source NOT NULL,   -- 'typed' | 'signal' ('voice' deprecated/unused)
  profile_is_strict         boolean NOT NULL DEFAULT false,     -- M1 (0003): "just give me cricket, nothing broader" — caps fallback (ranking-spec §2)
  profile_created_at        timestamptz NOT NULL DEFAULT now(),
  profile_updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_user_interest UNIQUE (profile_user_id, profile_interest_id)
);

CREATE INDEX idx_user_interest_profile_user ON user_interest_profile (profile_user_id);
```

### `user_interest_traits`  ← **DEPRECATED (voice onboarding dropped 2026-05-30)**
Purpose: non-category preference traits (ordering / depth). Was populated by the voice interview's `record_trait`. **Deprecated** — voice-agent onboarding was dropped; chip onboarding does not capture traits, and the active `reference/ranking-spec.md` does not consume them. The table shipped in migration 0003 (applied) and is **retained in the DB at defaults, unused** (not un-migrated, per the keep-DB-as-is decision).
Maps: `app.js VP_TURNS` trait detections — `world-first` and `context` (historical; no longer captured).

```sql
CREATE TABLE user_interest_traits (
  user_interest_traits_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  traits_user_id            uuid NOT NULL UNIQUE REFERENCES users (user_id) ON DELETE CASCADE,
  prefers_world_first       boolean NOT NULL DEFAULT true,       -- VP_TURNS 'world-first': big global headlines first
  prefers_context_over_facts boolean NOT NULL DEFAULT false,     -- VP_TURNS 'context': the "why" vs just facts
  context_vs_facts_ratio    numeric NOT NULL DEFAULT 0.5,        -- 0 = facts only, 1 = all context/"why"
  traits_updated_at         timestamptz NOT NULL DEFAULT now()
);
```

### ~~`onboarding_conversations`~~ — **DROPPED (voice onboarding cut 2026-05-30)**
The raw voice-onboarding transcript store. Voice-agent onboarding was dropped, so this table was **never created** — it had been deferred to the cancelled Phase 3c and was not part of migration 0003. No replacement: chip onboarding has no transcript.

### `follows`
Purpose: stories a user follows, with the last-seen marker that powers "what's new since you last watched".
Maps: `app.js S.followed` Set (seeded with `"s1"`) + the Following sheet + the "All caught up · WHILE YOU WERE OUT" card. `follow_last_seen_at` is the cutoff for the `● NEW` badge.

```sql
CREATE TABLE follows (
  follow_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  follow_user_id       uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  follow_story_id      text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  follow_created_at    timestamptz NOT NULL DEFAULT now(),
  follow_last_seen_at  timestamptz NOT NULL DEFAULT now(),       -- advanced when the user views the story/updates
  CONSTRAINT uq_follow UNIQUE (follow_user_id, follow_story_id)
);

CREATE INDEX idx_follows_user ON follows (follow_user_id);
```

#### "What's new since you last watched" query
Returns each followed story whose latest `story_timeline` event is newer than the user's `follow_last_seen_at` — exactly the `● NEW` badge logic in `app.js openFollowing()`:

```sql
SELECT
  s.story_id,
  s.story_headline,
  latest.timeline_what_text  AS latest_development_text,
  latest.timeline_event_at   AS latest_development_at,
  f.follow_last_seen_at
FROM follows f
JOIN stories s ON s.story_id = f.follow_story_id
JOIN LATERAL (
  SELECT t.timeline_what_text, t.timeline_event_at
  FROM story_timeline t
  WHERE t.timeline_story_id = s.story_id
  ORDER BY t.timeline_event_at DESC
  LIMIT 1
) latest ON true
WHERE f.follow_user_id = auth.uid()
  AND latest.timeline_event_at > f.follow_last_seen_at
ORDER BY latest.timeline_event_at DESC;
```

### `saves`
Purpose: stories a user saved (the Save action / "Saved" list in the profile sheet).
Maps: `app.js S.saved` Set + the `act-save` action + the Profile "Saved" row.

```sql
CREATE TABLE saves (
  save_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  save_user_id     uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  save_story_id    text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  save_created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_save UNIQUE (save_user_id, save_story_id)
);

CREATE INDEX idx_saves_user ON saves (save_user_id);
```

### `player_signals`
Purpose: implicit per-event engagement signals feeding category prioritization.
Maps: every interaction in `app.js` — `play`/`complete` (auto-advance), `open_detail` (swipe right), `ask` (Q&A), `voice` (swipe left), `save`, `follow`, `skip` (fast swipe up). Aligns with `EngagementSignal` in `api-contracts.md`. Reuse: TLDW `agents/memory/player_signals.py` (**ADAPT**, `reuse-map.md`).

```sql
CREATE TABLE player_signals (
  player_signal_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_user_id      uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  signal_story_id     text REFERENCES stories (story_id) ON DELETE SET NULL,
  event_type          player_signal_event NOT NULL,             -- play|complete|open_detail|ask|voice|save|follow|skip
  dwell_ms            integer,                                   -- time on the story (skip = small, complete = full)
  completion_pct      numeric,                                  -- signal_watch_completion_pct (0..1)
  occurred_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_player_signals_user_time  ON player_signals (signal_user_id, occurred_at DESC);
CREATE INDEX idx_player_signals_story      ON player_signals (signal_story_id);
CREATE INDEX idx_player_signals_event_type ON player_signals (event_type);
```

### `play_sessions`
Purpose: one row per briefing session; supports the 3×/week habit metric and the streak counter.
Maps: a full reel run from first tap → "All caught up · 30/30". Backs `users.user_streak_day_count` and the habit/retention analytics (the core "finite daily briefing you actually finish" product thesis).

```sql
CREATE TABLE play_sessions (
  play_session_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_user_id           uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  session_started_at        timestamptz NOT NULL DEFAULT now(),
  session_ended_at          timestamptz,
  session_stories_started   integer NOT NULL DEFAULT 0,
  session_stories_completed integer NOT NULL DEFAULT 0,          -- for "you actually finished" metric
  session_reached_caught_up boolean NOT NULL DEFAULT false,      -- hit the 30/30 finish line
  session_local_date        date NOT NULL DEFAULT (now() AT TIME ZONE 'utc')::date  -- for 3×/week bucketing
);

CREATE INDEX idx_play_sessions_user_date ON play_sessions (session_user_id, session_local_date DESC);
```

### `daily_feeds`  ← **the precomputed per-user feed (M1 re-scope, migration 0003)**
Purpose: one row per (user, story, position) for a given day — the personalized feed the reel reads. Written by the daily pipeline's allocator (Phase 1d SP4, `agents/pipeline/feed_assembly.py`) per `reference/ranking-spec.md` §3; read by `getDailyFeed(userId, feedDate)` in `src/lib/feed/supabaseFeed.ts` (Phase 1c SP4) into the unchanged `Story[]` contract. Ranking is **precomputed here, not a live RPC** (static-export client → trivial indexed read).
Maps: replaces the previous notion of a single global `feed_position` order — ordering is now per-user.

```sql
CREATE TABLE daily_feeds (
  daily_feed_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  feed_user_id             uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
  feed_story_id            text NOT NULL REFERENCES stories (story_id) ON DELETE CASCADE,
  feed_date                date NOT NULL,
  feed_position            smallint NOT NULL,                   -- 01..N order in the reel
  feed_score               numeric NOT NULL,                    -- the per-user Score that earned the slot (ranking-spec §1)
  feed_matched_interest_id uuid REFERENCES interests (interest_id) ON DELETE SET NULL,
  feed_slot_kind           text NOT NULL DEFAULT 'interest',    -- 'breaking' | 'interest' | 'exploration'
  feed_created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_daily_feed_position UNIQUE (feed_user_id, feed_date, feed_position),
  CONSTRAINT uq_daily_feed_story    UNIQUE (feed_user_id, feed_date, feed_story_id)
);

CREATE INDEX idx_daily_feeds_user_date ON daily_feeds (feed_user_id, feed_date, feed_position);
```

---

## 4. ER overview (text)

```
                         segments (segment_slug PK)
                              ▲            ▲
                              │            │ interest_segment_slug
              story_segment_slug           │
                              │       interests ──┐ parent_interest_id (self-FK tree)
   outlets ◀──primary_outlet─ stories ◀───────────┼───────────────┐
      ▲   ▲                    │  │ │ │ │ │ │      │               │
      │   │ source_outlet      │  │ │ │ │ │ │      │               │
      │   └── story_sources ───┘  │ │ │ │ │ │      │               │
      │                            │ │ │ │ │ │      │               │
      │   digests ◀── digest_story_id  │ │ │ │     │               │
      │      ▲                     │ │ │ │ │ │      │               │
      │      └ caption_sentences ──┘ │ │ │ │ │      │               │
      │      (word_tokens jsonb)     │ │ │ │ │      │               │
      │   detail_chunks ◀────────────┘ │ │ │ │      │               │
      │   story_trust ◀────────────────┘ │ │ │      │               │
      │   story_timeline ◀───────────────┘ │ │      │               │
      │   suggested_questions ◀────────────┘ │      │               │
      │   story_qa / story_topics ◀──────────┘      │               │
      │                                              │               │
   anchors (ALEX/JORDAN) ── referenced by caption_sentences.anchor_speaker (enum)
                                                     │               │
   auth.users (Supabase) ──1:1── users (user_id = auth.uid())        │
                                   │  ▲  ▲  ▲  ▲                     │
        user_interest_profile ─────┘  │  │  └── user_interest_profile.profile_interest_id ─┘
        user_interest_traits ─────────┘  │  (deprecated — voice onboarding dropped)
        follows / saves ──────────────────┘  (FK to stories)
        player_signals / play_sessions ───────┘
```

Cardinalities: `stories 1—N {digests, detail_chunks, story_timeline, story_sources, suggested_questions, story_qa, story_topics}`; `stories 1—1 story_trust`; `digests 1—N caption_sentences`; `interests 1—N interests` (self); `users 1—N {user_interest_profile, follows, saves, player_signals, play_sessions}`; `users 1—1 user_interest_traits`.

---

## 5. TLDW reuse map (which tables port a TLDW pattern)

Per `reference/reuse-map.md`:
- **`caption_sentences`** — the word-timing array comes from TLDW `agents/pipeline/stages/forced_alignment.py` (**PORT**). Same word-level timing data, now stored per sentence with `start_ms`/`end_ms`.
- **`story_qa` / `story_topics` / `story_sources`** — production answers come from TLDW `agents/rag/*` (chunker/embedder/retriever/pinecone) + `agents/pipeline/stages/verification.py` (**PORT**). These tables hold the curated/cached layer; the grounding contract is TLDW's.
- **`anchors`** — voices and the multi-speaker format come from TLDW `agents/voice/gemini_tts.py` (**PORT**): ALEX→`Leda`, JORDAN→`Sadaltager`.
- **`story_trust` / `story_timeline` / `outlets`** — the bias/coverage/blindspot layer is **NEW** (no TLDW analog) but `coverage_outlet_count` is fed by TLDW `agents/ingestion/dedup.py` cross-source clustering (**PORT**).
- **`player_signals` / `play_sessions`** — TLDW `agents/memory/player_signals.py` + `session_processor.py` (**ADAPT** for category prioritization).
- **`interests` / `user_interest_profile` / `user_interest_traits`** — seeded from TLDW `agents/shared/taxonomy.py` (**ADAPT** into the hierarchical tree); the rest is **NEW** (chip onboarding is new; `user_interest_traits` is deprecated — voice onboarding dropped).
- The Supabase migration *scaffolding* follows TLDW `supabase/` as a **PATTERN**; the schema itself is new (`reuse-map.md`: "News20 schema is new: stories, digests, sources, bias, follows, signals").

---

## 6. Row-Level Security (RLS)

Two tiers. **Content tables are public-read** (anyone can read the daily briefing; only the service-role worker writes). **User tables are private to the owner** via `auth.uid()`.

```sql
-- ── Tier 1: public-read content (read-only to clients; worker writes via service role) ──
-- Applies to: segments, outlets, stories, digests, caption_sentences, detail_chunks,
--             story_trust, story_timeline, story_sources, suggested_questions,
--             story_qa, story_topics, story_interests, anchors, interests.
ALTER TABLE stories ENABLE ROW LEVEL SECURITY;
CREATE POLICY stories_public_read ON stories
  FOR SELECT USING (true);
-- (no INSERT/UPDATE/DELETE policy → only the service-role key, which bypasses RLS, can write)
-- Repeat the identical public-read SELECT policy for each content table above.

-- ── Tier 2: per-user private tables ──
-- Applies to: users, follows, saves, player_signals, play_sessions,
--             user_interest_profile, user_interest_traits (deprecated — voice onboarding dropped).
-- daily_feeds is per-user but SELECT-self only (no write policy → only the service-role pipeline writes).

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY users_select_self ON users
  FOR SELECT USING (user_id = auth.uid());
CREATE POLICY users_update_self ON users
  FOR UPDATE USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

ALTER TABLE follows ENABLE ROW LEVEL SECURITY;
CREATE POLICY follows_owner_all ON follows
  FOR ALL USING (follow_user_id = auth.uid()) WITH CHECK (follow_user_id = auth.uid());

ALTER TABLE saves ENABLE ROW LEVEL SECURITY;
CREATE POLICY saves_owner_all ON saves
  FOR ALL USING (save_user_id = auth.uid()) WITH CHECK (save_user_id = auth.uid());

ALTER TABLE player_signals ENABLE ROW LEVEL SECURITY;
CREATE POLICY player_signals_owner_all ON player_signals
  FOR ALL USING (signal_user_id = auth.uid()) WITH CHECK (signal_user_id = auth.uid());

ALTER TABLE play_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY play_sessions_owner_all ON play_sessions
  FOR ALL USING (session_user_id = auth.uid()) WITH CHECK (session_user_id = auth.uid());

ALTER TABLE user_interest_profile ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_interest_profile_owner_all ON user_interest_profile
  FOR ALL USING (profile_user_id = auth.uid()) WITH CHECK (profile_user_id = auth.uid());

ALTER TABLE user_interest_traits ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_interest_traits_owner_all ON user_interest_traits
  FOR ALL USING (traits_user_id = auth.uid()) WITH CHECK (traits_user_id = auth.uid());

-- ── M1 re-scope (migration 0003): story_interests is public-read content; daily_feeds is read-self only ──
ALTER TABLE story_interests ENABLE ROW LEVEL SECURITY;
CREATE POLICY story_interests_public_read ON story_interests
  FOR SELECT USING (true);

ALTER TABLE daily_feeds ENABLE ROW LEVEL SECURITY;
CREATE POLICY daily_feeds_select_self ON daily_feeds
  FOR SELECT USING (feed_user_id = auth.uid());
-- (no INSERT/UPDATE/DELETE policy → only the service-role pipeline writes daily_feeds)
```

**Auth mapping (email-only magic-link pivot).** `users.user_id` is a FK to Supabase `auth.users.id`; `auth.uid()` returns that id for the authenticated request. Sessions are created by Supabase email OTP / magic-link (`supabase.auth.signInWithOtp({ email })`) — no password, no Sign-in-with-Apple. A `handle_new_user()` trigger on `auth.users` insert should create the matching `users` row (copying `user_email`) so the app profile exists immediately after the first magic-link click.

---

## 7. Notes for the porting team

- **No MP4 in the hot path.** The reel renders live from `digests.digest_audio_url` + `caption_sentences.word_tokens` + the ambient poster. `digest_legacy_mp4_url` exists only if a video render is also produced; do not make the client depend on it.
- **One current digest per story** is enforced by the partial unique index on `digests`. Regenerations insert a new row and flip `digest_is_current`.
- **Coverage counts vs `story.story_outlet_count`.** `story_trust.coverage_{left,center,right}_count` are the breakdown; `coverage_outlet_count` ("COVERED BY N OUTLETS") is the total. The denormalized `stories.story_outlet_count` is a render convenience; keep them consistent at write time.
- **Refusal contract.** Anything querying `story_qa` must honor `qa_is_grounded` and the topic gate (`story_topics`); off-topic free-text returns the refusal state, never a fabricated answer (Decision #5).
- **Word-token validation.** Validate `word_tokens` against a Pydantic model (`{word_text: str, is_highlight: bool, start_ms: int, end_ms: int}`) at the worker boundary before insert — never write raw dicts (conventions §types).
