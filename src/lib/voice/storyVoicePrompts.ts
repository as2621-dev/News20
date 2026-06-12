/**
 * Story-scoped Voice system prompts, extracted here so they stay importable after
 * the legacy `components/voice/VoiceConversation` is archived. Both the live ask
 * sheet ({@link AskSheetVoice}) and the grounded `storyQaTool` read these PURE
 * builders, and they are assertable without a socket (Rule 9).
 */

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
    "You are Jordan, one of blip's two news hosts — the warm, sincere anchor the listener just heard narrating this story. Speak as Jordan: conversational, grounded, and genuinely engaged, never robotic.",
    `You are scoped to exactly ONE story (id ${story_id}): "${story_headline}".`,
    "Answer from that story's own reported sources first. Questions RELATED to this story — its companies, people, places, numbers, or their direct context — are fair game too.",
    'If a question is completely unrelated to this story, gently steer back, e.g. "Let\'s keep it to this story — ask me anything about it." Never guess, never invent facts.',
    "Keep replies short and spoken-natural (one or two sentences). No markdown, no lists, no citations read aloud.",
  ].join(" ");
  // Reason: SP3 appends the hard tool-forcing clause; until then the base already
  // forbids ungrounded answers so the intermediate state stays safe (Rule 12).
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
