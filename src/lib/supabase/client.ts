/**
 * Browser Supabase client (Phase 1b SP4) for the reel's read-only feed queries
 * and (Phase 1e SP2) the authenticated email magic-link session.
 *
 * Uses the public anon key (safe in the browser) against the public-read content
 * tables and, post sign-in, against the user's own RLS-scoped rows. Reads
 * NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY so it runs in client
 * components. This is the only place the anon client is built.
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
    // Reason: Phase 1e SP2 — persist + auto-refresh keep the magic-link session
    // alive across reloads/relaunches (Capacitor WebView), and detectSessionInUrl
    // lets supabase-js parse the auth tokens out of the magic-link URL on the
    // static-export callback page (no server runtime to do it).
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  return cachedClient;
}
