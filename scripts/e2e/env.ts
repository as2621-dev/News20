/**
 * Minimal `.env` loader shared by the go-live E2E scripts (seed / drive / cleanup).
 *
 * The repo has no `dotenv` dependency (seed scripts are run with env pre-exported);
 * the E2E scripts are instead self-contained: they read the repo-root `.env` and
 * fill ONLY missing keys into `process.env` (already-exported values win). Values
 * are never logged (CLAUDE.md env-safety).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Absolute repo root (scripts/e2e/ -> two levels up). */
export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Where all E2E run state lives (gitignored — contains test credentials). */
export const E2E_STATE_DIR = path.join(REPO_ROOT, ".agents", "e2e", "state");

/**
 * Load `.env` from the repo root into `process.env` (non-overriding).
 *
 * @example
 *   loadDotEnv();
 *   const url = requireEnv("NEXT_PUBLIC_SUPABASE_URL");
 */
export function loadDotEnv(): void {
  let rawEnvFile: string;
  try {
    rawEnvFile = readFileSync(path.join(REPO_ROOT, ".env"), "utf8");
  } catch {
    return; // No .env — rely on exported env only.
  }
  for (const line of rawEnvFile.split("\n")) {
    const trimmedLine = line.trim();
    if (!trimmedLine || trimmedLine.startsWith("#")) {
      continue;
    }
    const equalsIndex = trimmedLine.indexOf("=");
    if (equalsIndex <= 0) {
      continue;
    }
    const envKey = trimmedLine.slice(0, equalsIndex).trim();
    let envValue = trimmedLine.slice(equalsIndex + 1).trim();
    if (
      (envValue.startsWith('"') && envValue.endsWith('"')) ||
      (envValue.startsWith("'") && envValue.endsWith("'"))
    ) {
      envValue = envValue.slice(1, -1);
    }
    if (process.env[envKey] === undefined) {
      process.env[envKey] = envValue;
    }
  }
}

/**
 * Read a required env var, failing loud (Rule 12) with the key name only —
 * never the value.
 */
export function requireEnv(envKey: string): string {
  const envValue = process.env[envKey];
  if (!envValue) {
    throw new Error(`Missing required env var ${envKey} — set it in .env (see .env.example).`);
  }
  return envValue;
}
