/**
 * Go-live E2E — seed the 4 test users (run: `npx tsx scripts/e2e/seed-test-users.ts`).
 *
 * For each profile in `scripts/e2e/profiles.json`:
 *   1. Creates the auth user via the service-role admin API with
 *      `email_confirm: true` + a generated per-run password — NO magic-link email is
 *      ever sent (Supabase built-in SMTP is rate-limited; the harness signs in with
 *      password from `drive-profile.ts` instead). Existing users get a password reset
 *      (idempotent re-run).
 *   2. Wipes every prior-run personalization row (`user_interest_profile`,
 *      `user_entity_follows`, `user_feed_allocation`, `daily_feeds`, `follows`) and
 *      resets `users.user_onboarded_at` to null, so EVERY run starts from a genuinely
 *      fresh onboarding state. It deliberately does NOT seed interests — the whole
 *      point of the journey is that the real UI writes them.
 *   3. Writes `.agents/e2e/state/test-users.json` (gitignored — holds credentials)
 *      for `drive-profile.ts` / `allocate_test_feeds.py` / `cleanup-test-users.ts`.
 *
 * Secrets: SUPABASE_URL (falls back to NEXT_PUBLIC_SUPABASE_URL) +
 * SUPABASE_SERVICE_ROLE_KEY, loaded from `.env` (never logged).
 */

import { randomBytes } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { E2E_STATE_DIR, loadDotEnv, REPO_ROOT, requireEnv } from "./env";

interface E2eProfileDefinition {
  profile_name: string;
  email: string;
  cdp_port: number;
}

interface SeededTestUser {
  profile_name: string;
  email: string;
  password: string;
  user_id: string;
}

/** Tables wiped per user (column → table), so re-runs start from fresh onboarding. */
const USER_SCOPED_TABLES: Array<{ table_name: string; user_column: string }> = [
  { table_name: "user_interest_profile", user_column: "profile_user_id" },
  { table_name: "user_entity_follows", user_column: "follow_user_id" },
  { table_name: "user_feed_allocation", user_column: "follow_user_id" },
  { table_name: "daily_feeds", user_column: "feed_user_id" },
  { table_name: "follows", user_column: "follow_user_id" },
];

/** Find an existing auth user id by email via the admin list API (paged). */
async function findAuthUserIdByEmail(supabase: SupabaseClient, email: string): Promise<string | null> {
  for (let pageNumber = 1; pageNumber <= 10; pageNumber += 1) {
    const { data, error } = await supabase.auth.admin.listUsers({ page: pageNumber, perPage: 200 });
    if (error) {
      throw new Error(`listUsers failed: ${error.message}`);
    }
    const match = data.users.find((authUser) => authUser.email?.toLowerCase() === email.toLowerCase());
    if (match) {
      return match.id;
    }
    if (data.users.length < 200) {
      return null;
    }
  }
  return null;
}

/** Create (or password-reset) one test user and wipe its personalization rows. */
async function seedOneTestUser(
  supabase: SupabaseClient,
  profile: E2eProfileDefinition,
  password: string,
): Promise<SeededTestUser> {
  let userId = await findAuthUserIdByEmail(supabase, profile.email);

  if (userId === null) {
    const { data, error } = await supabase.auth.admin.createUser({
      email: profile.email,
      password,
      email_confirm: true,
    });
    if (error || !data.user) {
      throw new Error(`createUser(${profile.profile_name}) failed: ${error?.message ?? "no user returned"}`);
    }
    userId = data.user.id;
    console.log(JSON.stringify({ event: "e2e_test_user_created", profile_name: profile.profile_name }));
  } else {
    const { error } = await supabase.auth.admin.updateUserById(userId, { password });
    if (error) {
      throw new Error(`updateUserById(${profile.profile_name}) failed: ${error.message}`);
    }
    console.log(JSON.stringify({ event: "e2e_test_user_password_reset", profile_name: profile.profile_name }));
  }

  for (const { table_name, user_column } of USER_SCOPED_TABLES) {
    const { error } = await supabase.from(table_name).delete().eq(user_column, userId);
    if (error) {
      throw new Error(`wipe ${table_name} for ${profile.profile_name} failed: ${error.message}`);
    }
  }

  // Fresh-onboarding state: the route gate must send this user into the flow.
  const { error: resetError } = await supabase
    .from("users")
    .update({ user_onboarded_at: null })
    .eq("user_id", userId);
  if (resetError) {
    throw new Error(`reset user_onboarded_at for ${profile.profile_name} failed: ${resetError.message}`);
  }

  console.log(
    JSON.stringify({ event: "e2e_test_user_seeded", profile_name: profile.profile_name, user_id: userId }),
  );
  return { profile_name: profile.profile_name, email: profile.email, password, user_id: userId };
}

async function main(): Promise<void> {
  loadDotEnv();
  const supabaseUrl = process.env.SUPABASE_URL ?? requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const serviceRoleKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });

  const profilesFile = JSON.parse(readFileSync(path.join(REPO_ROOT, "scripts", "e2e", "profiles.json"), "utf8")) as {
    profiles: E2eProfileDefinition[];
  };

  // One shared per-run password keeps the state file simple; it is rotated on every
  // seed run and only ever grants access to throwaway, tagged test accounts.
  const runPassword = `E2e!${randomBytes(12).toString("base64url")}`;

  const seededUsers: SeededTestUser[] = [];
  for (const profile of profilesFile.profiles) {
    seededUsers.push(await seedOneTestUser(supabase, profile, runPassword));
  }

  mkdirSync(E2E_STATE_DIR, { recursive: true });
  const stateFilePath = path.join(E2E_STATE_DIR, "test-users.json");
  writeFileSync(stateFilePath, `${JSON.stringify(seededUsers, null, 2)}\n`, { mode: 0o600 });
  console.log(
    JSON.stringify({
      event: "e2e_seed_completed",
      total_users: seededUsers.length,
      state_file: path.relative(REPO_ROOT, stateFilePath),
    }),
  );
}

main().catch((error: unknown) => {
  console.error(
    JSON.stringify({
      event: "e2e_seed_failed",
      error_message: error instanceof Error ? error.message : String(error),
      fix_suggestion: "Check SUPABASE_SERVICE_ROLE_KEY and that migrations 0003/0007/0008 are applied.",
    }),
  );
  process.exit(1);
});
