/**
 * Root (`/`) auth + onboarding gate resolver (Phase 4b SP2).
 *
 * The single decision the app home makes before it dares mount the reel: which of
 * the three states the visitor is in. {@link AppRouter} renders this decision; this
 * module owns ONLY the resolution, so the three routing cases are unit-testable
 * against a mocked session + `users.user_onboarded_at` (the phase DoD).
 *
 * The decision (spec §4b-SP2):
 *   - no session                       → `"sign_in"`   (→ email sign-in via the flow)
 *   - session, `user_onboarded_at` null → `"onboarding"` (→ interest chips)
 *   - session, `user_onboarded_at` set  → `"reel"`
 *
 * Degrade rule (Rule 12): a `users` read failure resolves to `"onboarding"`, never
 * `"reel"` — we must never flash the reel to someone we couldn't confirm is
 * onboarded. The onboarding flow itself re-checks and a truly-onboarded user's
 * `email` step skips straight through, so the degraded path self-heals.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { logger } from "@/lib/logger";
import { getCurrentSession } from "@/lib/supabase/auth";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** The three mutually-exclusive states the root route resolves to. */
export type RootGateDecision = "sign_in" | "onboarding" | "reel";

/**
 * Resolve which state the `/` visitor is in (see module docs).
 *
 * @param client - Optional Supabase client (injected in tests; defaults to the
 *   shared browser client).
 * @returns The {@link RootGateDecision} the router should render.
 *
 * @example
 * const decision = await resolveRootGate();
 * if (decision === "reel") {
 *   // mount the reel
 * }
 */
export async function resolveRootGate(client: SupabaseClient = getSupabaseBrowserClient()): Promise<RootGateDecision> {
  const session = await getCurrentSession(client);
  if (!session) {
    return "sign_in";
  }

  const { data, error } = await client
    .from("users")
    .select("user_onboarded_at")
    .eq("user_id", session.user.id)
    .maybeSingle();

  if (error) {
    // Reason (Rule 12): surface, then degrade to the onboarding flow rather than
    // trapping the user — and NEVER fall through to "reel" on an unconfirmed read.
    logger.error("root_gate_onboarded_check_failed", {
      error_message: error.message,
      fix_suggestion: "Confirm the users select-self RLS permits the read and the users row exists.",
    });
    return "onboarding";
  }

  return data?.user_onboarded_at ? "reel" : "onboarding";
}
