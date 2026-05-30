/**
 * Minimal structured JSON logger (shared across the frontend).
 *
 * Per CLAUDE.md §5 every operation logs a snake_case event name plus contextual
 * fields as a single JSON object to the console, so logs are greppable and
 * machine-parseable. Error/warn events carry a `fix_suggestion`. This is the TS
 * analogue of the Python `structlog` logger the agents use.
 *
 * Kept deliberately tiny — no transport, no levels config, no deps. The reel is
 * a static client bundle; this just standardizes the console shape.
 *
 * @example
 * logger.info("normalize_m0_captions_started", { digest_id: "digest-1" });
 * // → {"level":"info","event":"normalize_m0_captions_started","digest_id":"digest-1"}
 */

/** Structured log fields — JSON-serializable contextual key/values. */
type LogFields = Record<string, unknown>;

function emit(level: "info" | "warn" | "error", event: string, fields: LogFields): void {
  const payload = JSON.stringify({ level, event, ...fields });
  if (level === "error") {
    console.error(payload);
  } else if (level === "warn") {
    console.warn(payload);
  } else {
    console.log(payload);
  }
}

export const logger = {
  /** Informational event (normal control flow). */
  info(event: string, fields: LogFields = {}): void {
    emit("info", event, fields);
  },
  /** Warning event — anomalous but non-fatal; include a `fix_suggestion`. */
  warn(event: string, fields: LogFields = {}): void {
    emit("warn", event, fields);
  },
  /** Error event — include `error_message` and `fix_suggestion`. */
  error(event: string, fields: LogFields = {}): void {
    emit("error", event, fields);
  },
};
