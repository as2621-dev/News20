/**
 * Browser Supabase client (Phase 1b SP4) for the reel's read-only feed queries.
 *
 * Uses the public anon key (safe in the browser) against the public-read content
 * tables. Reads NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY so it
 * runs in client components. This is the only place the anon client is built.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let cachedClient: SupabaseClient | null = null;

/**
 * Get (or lazily create) the shared browser Supabase anon client.
 *
 * @returns A configured anon-key Supabase client.
 * @throws If the public env vars are missing.
 *
 * @example
 * const supabase = getSupabaseBrowserClient();
 * const { data } = await supabase.from("stories").select("*");
 */
export function getSupabaseBrowserClient(): SupabaseClient {
  if (cachedClient) {
    return cachedClient;
  }
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !anonKey) {
    throw new Error(
      "Missing env: NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY are required. " +
        "fix_suggestion: set both in .env.local (see .env.example).",
    );
  }
  cachedClient = createClient(supabaseUrl, anonKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return cachedClient;
}
