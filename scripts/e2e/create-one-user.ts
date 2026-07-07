/**
 * Create ONE test user by email (run: `npx tsx scripts/e2e/create-one-user.ts <email> [password]`).
 *
 * A focused, single-user sibling of `seed-test-users.ts` for when you just need one
 * throwaway account (e.g. `last@gmail.com`) without the full 4-profile go-live batch.
 *
 * It creates the auth user via the service-role admin API with `email_confirm: true`
 * so NO magic-link email is sent — there is therefore NO emailed 8-digit OTP for this
 * user. It signs in with a PASSWORD instead. The password defaults to the app's fixed
 * test code `123456` (see `TEST_AUTH_CODE` in `src/lib/supabase/auth.ts`) so the created
 * user signs straight in through the app's test-auth mode (`signInWithTestPassword`);
 * pass a second arg to override. An existing user is password-reset (idempotent re-run),
 * and `users.user_onboarded_at` is set to null so the route gate sends it into onboarding.
 *
 * Secrets: SUPABASE_URL (falls back to NEXT_PUBLIC_SUPABASE_URL) +
 * SUPABASE_SERVICE_ROLE_KEY, loaded from `.env` (never logged).
 */

import { createClient } from "@supabase/supabase-js";
import { loadDotEnv, requireEnv } from "./env";

/** Fixed app test code (mirrors TEST_AUTH_CODE) — the default password so test-mode sign-in works. */
const DEFAULT_TEST_PASSWORD = "123456";

async function main(): Promise<void> {
  const email = process.argv[2];
  if (!email || !email.includes("@")) {
    throw new Error("Usage: npx tsx scripts/e2e/create-one-user.ts <email> [password]");
  }
  const password = process.argv[3] ?? DEFAULT_TEST_PASSWORD;

  loadDotEnv();
  const supabaseUrl = process.env.SUPABASE_URL ?? requireEnv("NEXT_PUBLIC_SUPABASE_URL");
  const serviceRoleKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
  const supabase = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });

  // Find an existing auth user by email (admin list is paged; 2000 users is plenty for test envs).
  let userId: string | null = null;
  for (let pageNumber = 1; pageNumber <= 10 && userId === null; pageNumber += 1) {
    const { data, error } = await supabase.auth.admin.listUsers({ page: pageNumber, perPage: 200 });
    if (error) {
      throw new Error(`listUsers failed: ${error.message}`);
    }
    const match = data.users.find((authUser) => authUser.email?.toLowerCase() === email.toLowerCase());
    if (match) {
      userId = match.id;
    }
    if (data.users.length < 200) {
      break;
    }
  }

  if (userId === null) {
    const { data, error } = await supabase.auth.admin.createUser({ email, password, email_confirm: true });
    if (error || !data.user) {
      throw new Error(`createUser failed: ${error?.message ?? "no user returned"}`);
    }
    userId = data.user.id;
    console.log(JSON.stringify({ event: "test_user_created", user_id: userId }));
  } else {
    const { error } = await supabase.auth.admin.updateUserById(userId, { password });
    if (error) {
      throw new Error(`updateUserById failed: ${error.message}`);
    }
    console.log(JSON.stringify({ event: "test_user_password_reset", user_id: userId }));
  }

  // Fresh-onboarding state: the route gate must send this user into the flow.
  const { error: resetError } = await supabase
    .from("users")
    .update({ user_onboarded_at: null })
    .eq("user_id", userId);
  if (resetError) {
    throw new Error(`reset user_onboarded_at failed: ${resetError.message}`);
  }

  // Credentials to console (email + password ARE the sign-in secret for this throwaway account).
  console.log(JSON.stringify({ event: "test_user_ready", email, password, user_id: userId }));
}

main().catch((error: unknown) => {
  console.error(
    JSON.stringify({
      event: "create_one_user_failed",
      error_message: error instanceof Error ? error.message : String(error),
      fix_suggestion: "Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in .env (see .env.example) and retry.",
    }),
  );
  process.exit(1);
});
