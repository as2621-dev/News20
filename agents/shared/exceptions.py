"""Custom exception classes for the News20 agent code.

Ported from TLDW (`agents/shared/exceptions.py`) and trimmed to the subset the
M0 quality-spike TTS spine raises (Rule 2 — minimum code): the base error, the
pipeline-stage base, and the TTS render error. The RAG / ingestion / clustering
/ quality-gate exceptions TLDW defines are out of scope for this spike and are
omitted; reintroduce them when those stages are ported.

Exception hierarchy:
    VoiceAgentError (base)
    +-- PipelineStageError      -- Base pipeline-stage failure
        +-- TTSRenderError          -- TTS rendering / audio assembly failure

Example:
    >>> from agents.shared.exceptions import TTSRenderError
    >>> raise TTSRenderError(
    ...     message="Gemini multi-speaker TTS returned no audio bytes",
    ...     audio_step="gemini_multispeaker_tts",
    ...     fix_suggestion="Check Gemini TTS quota and GEMINI_API_KEY validity",
    ... )
"""


class VoiceAgentError(Exception):
    """Base exception for all News20 agent errors.

    Attributes:
        message: Human-readable error description.
        fix_suggestion: Actionable suggestion for resolving the error.
    """

    def __init__(self, message: str, fix_suggestion: str = "") -> None:
        self.message = message
        self.fix_suggestion = fix_suggestion
        super().__init__(message)

    def __str__(self) -> str:
        if self.fix_suggestion:
            return f"{self.message} | fix_suggestion: {self.fix_suggestion}"
        return self.message


class PipelineStageError(VoiceAgentError):
    """Base exception for pipeline-stage errors.

    Subclassed by stage-specific exceptions. Includes the stage name for
    structured logging and debugging.

    Attributes:
        stage: Name or number of the pipeline stage that failed.

    Example:
        >>> raise PipelineStageError(
        ...     stage="tts_handoff",
        ...     message="No dialogue turns provided",
        ...     fix_suggestion="Verify the digest script is non-empty",
        ... )
    """

    def __init__(
        self,
        stage: str,
        message: str,
        fix_suggestion: str = "Check pipeline logs for the specific stage failure",
    ) -> None:
        self.stage = stage
        super().__init__(
            message=f"[Stage: {stage}] {message}", fix_suggestion=fix_suggestion
        )


class TTSRenderError(PipelineStageError):
    """Raised when TTS rendering or audio assembly fails.

    Common causes: Gemini TTS API error, empty audio response, FFmpeg failure,
    or an invalid / oversized chunk.

    Attributes:
        audio_step: The specific TTS/audio step that failed
            (e.g., "gemini_multispeaker_tts", "render_chunk", "assembly").

    Example:
        >>> raise TTSRenderError(
        ...     message="Gemini multi-speaker TTS returned no audio bytes",
        ...     audio_step="gemini_multispeaker_tts",
        ... )
    """

    def __init__(
        self,
        message: str,
        audio_step: str = "",
        fix_suggestion: str = "Check Gemini TTS key, service status, and FFmpeg installation",
    ) -> None:
        self.audio_step = audio_step
        super().__init__(
            stage="tts_handoff",
            message=f"[{audio_step}] {message}" if audio_step else message,
            fix_suggestion=fix_suggestion,
        )


# ---------------------------------------------------------------------------
# Ingestion exceptions (Phase 1d SP1 — reintroduced from TLDW as the ingestion
# stage is ported; see reference/reuse-map.md "Ingestion").
# ---------------------------------------------------------------------------


class VerificationHaltError(PipelineStageError):
    """Raised when the verification guardrail blocks a digest from publishing.

    The hallucination guardrail (reference/reuse-map.md Decision #5) refuses to
    ship a digest whose script makes claims the single source does not support.
    The orchestrator (SP3) catches this to skip/rollback the offending story
    rather than publish ungrounded narration.

    Attributes:
        unsupported_count: Number of UNSUPPORTED claims in the script.
        contradicted_count: Number of CONTRADICTED claims in the script.

    Example:
        >>> raise VerificationHaltError(
        ...     unsupported_count=1,
        ...     contradicted_count=0,
        ... )
    """

    def __init__(
        self,
        unsupported_count: int,
        contradicted_count: int,
        fix_suggestion: str = "Regenerate the script constrained to the single source, "
        "or drop this story from the batch",
    ) -> None:
        self.unsupported_count = unsupported_count
        self.contradicted_count = contradicted_count
        message = (
            f"verification blocked the digest: {unsupported_count} unsupported + "
            f"{contradicted_count} contradicted claim(s) not grounded in the source"
        )
        super().__init__(
            stage="verification", message=message, fix_suggestion=fix_suggestion
        )


class IngestionError(VoiceAgentError):
    """Base exception for the news ingestion stage.

    Raised when the interest-keyed ingestion pipeline cannot proceed — e.g.,
    the active-interest set is empty (no user profiles), or an interest node
    referenced for ancestor tagging is missing from the taxonomy.

    Example:
        >>> raise IngestionError(
        ...     message="Active-interest set is empty — no user profiles to ingest for",
        ...     fix_suggestion="Seed at least one user_interest_profile (Phase 1e) before ingesting",
        ... )
    """


class AdapterFetchError(IngestionError):
    """Raised when a news source adapter fails to fetch or extract content.

    Common causes: the source API returns an HTTP error, times out, or returns
    a non-parseable body (e.g., GDELT's rate-limit plaintext notice instead of
    JSON).

    Attributes:
        adapter_name: The adapter that raised the error (e.g., "gdelt_doc").

    Example:
        >>> raise AdapterFetchError(
        ...     message="GDELT returned a rate-limit notice instead of JSON",
        ...     adapter_name="gdelt_doc",
        ...     fix_suggestion="Throttle to <=1 request / 5s and retry",
        ... )
    """

    def __init__(
        self,
        message: str,
        adapter_name: str = "",
        fix_suggestion: str = "Check the source API status, the request rate, and network connectivity",
    ) -> None:
        self.adapter_name = adapter_name
        super().__init__(
            message=f"[{adapter_name}] {message}" if adapter_name else message,
            fix_suggestion=fix_suggestion,
        )


# ---------------------------------------------------------------------------
# Grounded Q&A exceptions (Phase 2b SP1 — the per-story grounding corpus loader,
# agents/qa/corpus.py).
# ---------------------------------------------------------------------------


class GroundingCorpusError(VoiceAgentError):
    """Base exception for the grounded-Q&A corpus loader (agents/qa/corpus.py).

    Raised when a story's grounding corpus cannot be assembled — e.g. the
    ``story_id`` has no readable grounding text at all (no ``detail_chunks`` and
    no other passages), so there is nothing to ground an answer on.

    Attributes:
        story_id: The story whose corpus could not be loaded.

    Example:
        >>> raise GroundingCorpusError(
        ...     story_id="s1",
        ...     message="story 's1' has no grounding passages to load",
        ...     fix_suggestion="Confirm detail_chunks are seeded for this story (M1 Phase 1b)",
        ... )
    """

    def __init__(
        self,
        story_id: str,
        message: str,
        fix_suggestion: str = "Confirm the story_id exists and its grounding text is seeded",
    ) -> None:
        self.story_id = story_id
        super().__init__(
            message=f"[story_id={story_id}] {message}", fix_suggestion=fix_suggestion
        )


class CorpusBudgetExceededError(GroundingCorpusError):
    """Raised when a story's assembled corpus exceeds the char budget (fail loud).

    The per-story corpus is loaded whole into the LLM context, so it must stay
    small (``plans/phase-2b-m2-grounded-interrogation.md`` re-scope, Rule 12 —
    fail loud, never silently truncate). Exceeding the budget signals a story
    that has outgrown the in-context grounding approach (the documented escape
    hatch: reintroduce retrieval behind the same endpoint contract).

    Attributes:
        total_char_count: The assembled corpus's actual character count.
        char_budget: The maximum allowed character count.

    Example:
        >>> raise CorpusBudgetExceededError(
        ...     story_id="s1",
        ...     total_char_count=31000,
        ...     char_budget=24000,
        ... )
    """

    def __init__(
        self,
        story_id: str,
        total_char_count: int,
        char_budget: int,
        fix_suggestion: str = "Trim the story's grounding text, or reintroduce retrieval "
        "behind the same endpoint contract (the phase-2b escape hatch)",
    ) -> None:
        self.total_char_count = total_char_count
        self.char_budget = char_budget
        message = (
            f"grounding corpus is {total_char_count} chars, over the "
            f"{char_budget}-char budget — too large to load whole into context"
        )
        super().__init__(
            story_id=story_id, message=message, fix_suggestion=fix_suggestion
        )
