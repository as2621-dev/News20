/**
 * Story-scoped Voice system prompts, extracted here so they stay importable after
 * the legacy `components/voice/VoiceConversation` is archived. Both the live ask
 * sheet ({@link AskSheetVoice}) and the grounded `storyQaTool` read these PURE
 * builders, and they are assertable without a socket (Rule 9).
 */

/**
 * Build the shared Jordan persona + single-story scope lines.
 *
 * Factored out so {@link buildInNewsSystemInstruction} (tool-only) and
 * {@link buildInNewsSystemInstructionWithCorpus} (corpus-in-context) read the SAME
 * persona/voice/scope text — the voice can't drift between the two paths if the
 * string lives in exactly one place (Rule 3). The per-builder reply-style and
 * grounding lines are appended by each caller, NOT included here.
 *
 * @param story_headline - The active story's headline, naming the scope to the model.
 * @param story_id - The active story's id, embedded for scope clarity.
 * @returns The persona + scope prefix (no trailing reply-style/grounding lines).
 */
function buildPersonaScopePrefix(story_headline: string, story_id: string): string {
  return [
    "You are Jordan, one of blip's two news hosts — the warm, sincere anchor the listener just heard narrating this story. Speak as Jordan: conversational, grounded, and genuinely engaged, never robotic.",
    `You are scoped to exactly ONE story (id ${story_id}): "${story_headline}".`,
  ].join(" ");
}

/** The shared reply-style line — short, spoken, no markdown, no citations aloud. */
const REPLY_STYLE_LINE =
  "Keep replies short and spoken-natural (one or two sentences). No markdown, no lists, no citations read aloud.";

/**
 * Build the in-news Voice system instruction for one story.
 *
 * PURE + exported so the AUDIO/Charon/story-scope contract is assertable without a
 * socket (Rule 9), and so SP3 can extend the grounding clause without forking the
 * base persona. The instruction scopes the model to the SINGLE active story, tells
 * it to answer ONLY from that story's sources, and to refuse cleanly otherwise —
 * the trust contract (Decision #5) the SP3 tool will then enforce mechanically.
 *
 * @param story_headline - The active story's headline, naming the scope to the model.
 * @param story_id - The active story's id (logged into the instruction for scope clarity).
 * @param tool_grounding_clause - Optional clause SP3 appends to FORBID answering
 *   without calling the grounded-answer tool. Omitted in SP2 (no tool yet); the
 *   base instruction already leans refusal-safe so the tool-less state can't invent.
 * @returns The system-instruction string for the `setup` frame.
 *
 * @example
 * buildInNewsSystemInstruction("Ceasefire talks stall", "s1");
 * // "You are blip, a calm hands-free news companion… about exactly one story…"
 */
export function buildInNewsSystemInstruction(
  story_headline: string,
  story_id: string,
  tool_grounding_clause?: string,
): string {
  const base = [
    buildPersonaScopePrefix(story_headline, story_id),
    "Answer from that story's own reported sources first. Questions RELATED to this story — its companies, people, places, numbers, or their direct context — are fair game too.",
    'If a question is completely unrelated to this story, gently steer back, e.g. "Let\'s keep it to this story — ask me anything about it." Never guess, never invent facts.',
    REPLY_STYLE_LINE,
  ].join(" ");
  // Reason: SP3 appends the hard tool-forcing clause; until then the base already
  // forbids ungrounded answers so the intermediate state stays safe (Rule 12).
  return tool_grounding_clause ? `${base} ${tool_grounding_clause}` : base;
}

/**
 * Build the corpus-in-context in-news Voice system instruction for one story.
 *
 * The latency-fix path (voice-latency-hybrid-grounding): the whole grounding corpus
 * is injected into the `setup` frame so the native-audio model answers
 * corpus-answerable questions DIRECTLY — grounded by construction, no Railway hop.
 *
 * PURE + exported so the corpus-embedding contract is unit-assertable without a
 * socket (Rule 9). Reuses {@link buildInNewsSystemInstruction}'s exact persona via
 * {@link buildPersonaScopePrefix} + {@link REPLY_STYLE_LINE}, so the voice stays
 * identical across the two paths (no string drift, Rule 3).
 *
 * **Single graceful-degradation seam (Rule 12).** When `corpus_context_block` is
 * empty/whitespace, this delegates to {@link buildInNewsSystemInstruction} (the
 * tool-only path) — so an empty corpus from {@link fetchStoryCorpus} transparently
 * yields today's behavior, here and nowhere else.
 *
 * @param story_headline - The active story's headline, naming the scope to the model.
 * @param story_id - The active story's id, embedded for scope clarity.
 * @param corpus_context_block - The `[passage_id] text` lines from the SP1 endpoint.
 *   Empty/whitespace ⇒ tool-only fallback.
 * @param tool_grounding_clause - Optional clause SP3 supplies (the corpus-first /
 *   tool-on-miss rule); appended last so the model reads it after the corpus.
 * @returns The system-instruction string for the `setup` frame.
 *
 * @example
 * buildInNewsSystemInstructionWithCorpus("Ceasefire talks stall", "s1", "[p0] Talks stalled.");
 * // "You are Jordan… STORY CONTEXT (answer ONLY from this…): [p0] Talks stalled. …"
 */
export function buildInNewsSystemInstructionWithCorpus(
  story_headline: string,
  story_id: string,
  corpus_context_block: string,
  tool_grounding_clause?: string,
): string {
  // Reason: the single graceful-degradation seam — an empty corpus is the only
  // signal we need to fall back to today's tool-only voice (Rule 12).
  if (corpus_context_block.trim().length === 0) {
    return buildInNewsSystemInstruction(story_headline, story_id, tool_grounding_clause);
  }

  const base = [
    buildPersonaScopePrefix(story_headline, story_id),
    "Answer directly from the STORY CONTEXT below — it is this story's full reported source material.",
    `STORY CONTEXT (answer ONLY from this; each line is [passage_id] text):\n${corpus_context_block}`,
    "Never use outside knowledge. Never read passage ids or citations aloud.",
    REPLY_STYLE_LINE,
  ].join(" ");
  // Reason: SP3 supplies the corpus-first / tool-on-miss clause; appended last so
  // the model reads the corpus before the rule for when to fall back to the tool.
  return tool_grounding_clause ? `${base} ${tool_grounding_clause}` : base;
}

/**
 * Build the story-specific greeting nudge (gotcha 4 — forces the model's first
 * line, since auto-VAD otherwise waits for user audio). PURE + exported so the
 * "greeting is about THIS story" contract is testable.
 *
 * @param story_headline - The active story's headline.
 * @returns A short spoken-greeting instruction naming this story.
 *
 * @example
 * buildGreetingNudge("Ceasefire talks stall");
 * // "Greet me in one short sentence and invite me to ask about this story: …"
 */
export function buildGreetingNudge(story_headline: string): string {
  return `Greet me in one short, friendly sentence and invite me to ask anything about this story: "${story_headline}". Do not summarize it yet.`;
}
