import { describe, expect, it, vi } from "vitest";
import { resolveRootGate } from "@/lib/auth/routeGuard";

/**
 * Fake Supabase client exposing only the surface `resolveRootGate` touches:
 * `auth.getSession()` (via `getCurrentSession`) and the
 * `from("users").select(...).eq(...).maybeSingle()` read chain. Mocks at the
 * client boundary (CLAUDE.md mocking strategy), mirroring
 * `tests/lib/supabase/auth.test.ts`.
 *
 * @param params.session - The session `getSession` resolves to (`null` = signed out).
 * @param params.usersResult - The `{ data, error }` the `users` read resolves to.
 */
function makeFakeClient(params: {
  session: { user: { id: string } } | null;
  usersResult?: { data: { user_onboarded_at: string | null } | null; error: { message: string } | null };
}) {
  const maybeSingle = vi.fn().mockResolvedValue(params.usersResult ?? { data: null, error: null });
  const eq = vi.fn().mockReturnValue({ maybeSingle });
  const select = vi.fn().mockReturnValue({ eq });
  const from = vi.fn().mockReturnValue({ select });
  const getSession = vi.fn().mockResolvedValue({ data: { session: params.session } });
  // Reason: the fake implements only the two surfaces the resolver uses; `as never`
  // satisfies the SupabaseClient type at this boundary without stubbing the whole client.
  return { client: { auth: { getSession }, from } as never, from, eq };
}

describe("resolveRootGate", () => {
  it("returns 'sign_in' and never reads users when there is no session", async () => {
    // WHY: a signed-out visitor must hit the email sign-in flow, and the gate must
    // NOT spend a users read it can't scope (no user id). Fails if the no-session
    // short-circuit is removed.
    const { client, from } = makeFakeClient({ session: null });

    const decision = await resolveRootGate(client);

    expect(decision).toBe("sign_in");
    expect(from).not.toHaveBeenCalled();
  });

  it("returns 'onboarding' for an authed user whose user_onboarded_at is null", async () => {
    // WHY: this is the gate's whole purpose — an authed but un-onboarded user is sent
    // to interest chips, never the reel. Fails if a null stamp leaks through to "reel".
    const { client, eq } = makeFakeClient({
      session: { user: { id: "user-123" } },
      usersResult: { data: { user_onboarded_at: null }, error: null },
    });

    const decision = await resolveRootGate(client);

    expect(decision).toBe("onboarding");
    // The read must be scoped to the authed user, never a global/other-user row.
    expect(eq).toHaveBeenCalledWith("user_id", "user-123");
  });

  it("returns 'reel' for an authed user with a user_onboarded_at stamp", async () => {
    // WHY: the only path that mounts the reel — a confirmed onboarded user. Fails if
    // a present stamp is misread as not-onboarded (which would trap the user in chips).
    const { client } = makeFakeClient({
      session: { user: { id: "user-123" } },
      usersResult: { data: { user_onboarded_at: "2026-06-01T00:00:00Z" }, error: null },
    });

    const decision = await resolveRootGate(client);

    expect(decision).toBe("reel");
  });

  it("degrades to 'onboarding' (never 'reel') when the users read errors (Rule 12)", async () => {
    // WHY: an unconfirmed onboarded-status must NEVER flash the reel. A users read
    // failure resolves to the onboarding flow, not the reel. Fails if the error branch
    // falls through to "reel" or throws instead of degrading.
    const { client } = makeFakeClient({
      session: { user: { id: "user-123" } },
      usersResult: { data: null, error: { message: "permission denied for table users" } },
    });

    const decision = await resolveRootGate(client);

    expect(decision).toBe("onboarding");
  });
});
