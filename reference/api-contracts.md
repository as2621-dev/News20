# API Contracts

**Why this doc exists:** The frontend (Next.js SPA in Capacitor) and the backend (Supabase + Python worker) must agree on shared shapes. These types are the contract; keep them in sync with `src/types/` (TS) and the Pydantic models (Python). Verbose, prefixed field names per CLAUDE.md.

**When to update:** Whenever a stored entity or an API response shape changes. Update both the TS interface and the Pydantic model together.

## Core entities

```typescript
// A real-world story, clustered across many outlets.
interface Story {
  story_id: string;
  story_headline: string;
  story_dek: string;                 // one-line subhead
  story_category: InterestCategory;  // e.g. "tech" | "politics" | "finance"
  story_first_reported_utc: string;
  story_last_updated_utc: string;
  outlet_count: number;              // "42 outlets covering this"
  bias_breakdown: BiasBreakdown;
  blindspot_flag: boolean;           // true if >70% one side
}

interface BiasBreakdown {
  left_pct: number;
  center_pct: number;
  right_pct: number;
}

// One canonical generated video per story (served to many users).
interface Digest {
  digest_id: string;
  story_id: string;
  digest_mp4_url: string;            // Supabase storage / CDN
  digest_duration_seconds: number;   // ~55
  digest_caption_track_url: string;  // word-by-word timing (forced alignment)
  digest_generated_utc: string;
}

// Swipe-right destination payload.
interface StoryDetail {
  story_id: string;
  readable_text_chunks: string[];    // chunked; total < ~100s read
  detail_visuals: DetailVisual[];    // graph / timeline / images
  sources: StorySource[];            // sortable by bias + recency
}

interface DetailVisual {
  visual_type: "graph" | "timeline" | "image" | "chart";
  visual_url: string;
  visual_caption?: string;
}

interface StorySource {
  source_outlet_name: string;
  source_url: string;
  source_bias: "left" | "center" | "right";
  source_published_utc: string;
}

type InterestCategory =
  | "tech" | "science" | "politics" | "ai"
  | "finance" | "sports" | "world" | "business";

interface FollowState {
  story_id: string;
  is_followed: boolean;
  has_update_since_last_watch: boolean;  // "what's new since you last watched"
}
```

## Interrogation (typed Q&A + voice) — RAG-grounded

```typescript
interface QuestionRequest {
  story_id: string;
  question_text: string;
  conversation_id?: string;          // for multi-turn
}

interface QuestionAnswer {
  answer_text: string;
  answer_citations: AnswerCitation[]; // every answer cites the source
  answer_is_grounded: boolean;        // false → refused/insufficient source
}

interface AnswerCitation {
  source_url: string;
  source_quote: string;
}
```
**Contract rule (Decision #5):** if `answer_is_grounded` is false, the UI must show a graceful "the source doesn't cover that" state — never present an ungrounded guess as fact.

## Implicit signals (personalization input)

```typescript
interface EngagementSignal {
  user_id: string;
  story_id: string;
  signal_watch_completion_pct: number;
  signal_swiped_right: boolean;       // curiosity
  signal_swiped_left_voice: boolean;  // deep engagement
  signal_swipe_up_speed_ms: number;   // fast skip = negative
  signal_asked_question: boolean;
}
```

## Error response shape (CLAUDE.md standard)

```typescript
interface ErrorResponse {
  error_code: string;
  error_message: string;
  error_details?: Record<string, unknown>;
  timestamp_utc: string;
}
```
Python side raises typed exceptions (`agents/shared/exceptions.py`) and serializes to this shape at the worker/API boundary.
