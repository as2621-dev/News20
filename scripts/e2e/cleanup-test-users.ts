/**
 * Go-live E2E — clean up test-user data (run: `npx tsx scripts/e2e/cleanup-test-users.ts [--purge]`).
 *
 * Default: wipes every personalization row the run created (same tables as the seed
 * script) but KEEPS the auth users + `users` rows, so the next `/go-live-check` run
 * re-seeds quickly and the user can still sign in manually to review reels.
 *
 * `--purge`: additionally deletes the `users` rows and the auth users themselves —
 * the full leave-no-trace teardown (use before shipping / when retiring profiles).
 *
 * Reads `.agents/e2e/state/test-users.json`; falls back to resolving by the emails
 * in `scripts/e2e/profiles.json` when the state file is missing.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { E2E_STATE_DIR, loadDotEnv, REPO_ROOT, requireEnv } from "./env";

const USER_SCOPED_TABLES: Array<{ table_name: string; user_column: string }> = [
  { table_name: "user_interest_profile", user_column: "profile_user_id" },
  { table_name: "user_entity_follows", user_column: "follow_user_id" },
  { table_name: "user_feed_allocation", user_column: "follow_user_id" },
  { table_name: "daily_feeds", user_column: "feed_user_id" },
  { table_name: "follows", user_column: "follow_user_id" },
];

interface CleanupTarget {
  profile_name: string;
  email: string;
  user_id: string;
}

/** Resolve cleanup targets from the state file, or by email lookup as a fallback. */
async function resolveTargets(supabase: SupabaseClient): Promise<CleanupTarget[]> {
  const stateFilePath = path.join(E2E_STATE_DIR, "test-users.json");
  if (existsSync(stateFilePath)) {
    return JSON.parse(readFileSync(stateFilePath, "utf8")) as CleanupTarget[];
  }

  const profilesFile = JSON.parse(readFileSync(path.join(REPO_ROOT, "scripts", "e2e", "profiles.json"), "utf8")) as {
    profiles: Array<{ profile_name: string; email: string }>;
  };
  const targets: CleanupTarget[] = [];
  const { data, error } = await supabase.auth.admin.listUsers({ page: 1, perPage: 200 });
  if (error) {
    throw new Error(`listUsers failed: ${error.message}`);
  }
  for (const profile of profilesFile.profiles) {
    const match = data.users.find((authUser) => authUser.email?.toLowerCase() === profile.email.toLowerCase());
    if (match) {
      targets.push({ profile_name: profile.profile_name, email: profile.email, user_id: match.id });
    }
  }
  return targets;
}

async function main(): Promise<void> {
  loadDotEnv();
  const shouldPurge = process.argv.includes("--purge");
  const supabaseUrl = process.env.SUPABASE_URL ?? requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const serviceRoleKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });

  const targets = await resolveTargets(supabase);
  if (targets.length === 0) {
    console.log(JSON.stringify({ event: "e2e_cleanup_nothing_to_do" }));
    return;
  }

  for (const target of targets) {
    for (const { table_name, user_column } of USER_SCOPED_TABLES) {
      const { error } = await supabase.from(table_name).delete().eq(user_column, target.user_id);
      if (error) {
        throw new Error(`wipe ${table_name} for ${target.profile_name} failed: ${error.message}`);
      }
    }

    if (shouldPurge) {
      const { error: usersRowError } = await supabase.from("users").delete().eq("user_id", target.user_id);
      if (usersRowError) {
        throw new Error(`delete users row for ${target.profile_name} failed: ${usersRowError.message}`);
      }
      const { error: authError } = await supabase.auth.admin.deleteUser(target.user_id);
      if (authError) {
        throw new Error(`deleteUser for ${target.profile_name} failed: ${authError.message}`);
      }
    }

    console.log(
      JSON.stringify({ event: "e2e_test_user_cleaned", profile_name: target.profile_name, purged: shouldPurge }),
    );
  }

  console.log(JSON.stringify({ event: "e2e_cleanup_completed", total_users: targets.length, purged: shouldPurge }));
}

main().catch((error: unknown) => {
  console.error(
    JSON.stringify({
      event: "e2e_cleanup_failed",
      error_message: error instanceof Error ? error.message : String(error),
      fix_suggestion: "Check SUPABASE_SERVICE_ROLE_KEY; re-run with the state file present if email lookup failed.",
    }),
  );
  process.exit(1);
});
