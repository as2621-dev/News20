"""Structured JSON logging configuration using structlog.

Ported verbatim from TLDW (`agents/shared/logger.py`). Configures structlog
to output JSON to stdout with UTC timestamps, log levels, and caller
information. All log events should use snake_case event names and include
``fix_suggestion`` on errors.

Example:
    >>> from agents.shared.logger import get_logger
    >>> logger = get_logger()
    >>> logger.info("pipeline_started", digest_id="digest-1", turn_count=14)
    >>> logger.error(
    ...     "tts_render_failed",
    ...     digest_id="digest-1",
    ...     error_type="TTSRenderError",
    ...     fix_suggestion="Check Gemini TTS quota and GEMINI_API_KEY validity",
    ... )
"""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog with JSON output, UTC timestamps, and caller info.

    This function sets up both structlog and the stdlib logging module
    to output structured JSON to stdout. Call once at application startup.

    Args:
        log_level: Minimum log level to emit (e.g., "DEBUG", "INFO", "WARNING").

    Example:
        >>> configure_logging(log_level="DEBUG")
    """
    # Reason: shared processors run in both structlog and stdlib pipelines
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.MODULE,
            ],
        ),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Reason: route stdlib logging through structlog's JSON formatter
    # so that third-party library logs (httpx, google-genai, etc.) also
    # appear as structured JSON.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Returns a bound structlog logger configured for JSON output.
    Call configure_logging() once at application startup before using this.

    Args:
        name: Optional logger name. If None, uses the calling module's name.

    Returns:
        A bound structlog logger that outputs structured JSON to stdout.

    Example:
        >>> logger = get_logger("voice.gemini_tts")
        >>> logger.info("gemini_tts_chunk_started", chunk_index=0)
    """
    # Reason: auto-configure on first use so callers don't need to
    # remember to call configure_logging() during development/testing.
    if not structlog.is_configured():
        configure_logging()

    return structlog.get_logger(name)
