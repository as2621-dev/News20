import { describe, expect, it, vi } from "vitest";
import { getUserInterests } from "@/lib/interests";

/**
 * getUserInterests — the read backing the Sources surface's interest chips.
 *
 * WHY these tests (Rule 9 — encode the product contract): the chips must (1)
 * carry the LOCKED category color so a Markets pick is unmistakably green and a
 * Tech pick cyan (the whole point of the user's "permanent colors" ask), (2) lead
 * with root categories so the colored chips read first, and (3) degrade to an
 * empty chip row when signed out rather than throwing (the Sources surface must
 * still paint — Rule 12). A regression in the segment→hex map or the sort would
 * silently mis-color or mis-order the chips, so we assert color + order directly.
 *
 * Mocks Supabase at the client boundary (CLAUDE.md mocking strategy).
 */

/** One `user_interest_profile ⋈ interests` row as the embedded select returns it. */
interface ProfileInterestRow {
  interests: {
    interest_id: string;
    interest_label: string;
    depth_level: number;
    interest_segment_slug: string | null;
  } | null;
}

/** A chainable fake of the one read getUserInterests performs, plus auth.getUser. */
function makeClient(options: {
  user: { id: string } | null;
  rows?: ProfileInterestRow[];
  error?: { message: string };
}) {
  const builder = {
    select: () => builder,
    eq: () => builder,
    returns: vi.fn().mockResolvedValue({ data: options.rows ?? [], error: options.error ?? null }),
  };
  return {
    auth: { getUser: vi.fn().mockResolvedValue({ data: { user: options.user }, error: null }) },
    from: () => builder,
  } as never;
}

describe("getUserInterests (DB → colored interest chips)", () => {
  it("maps each pick to the locked category color and leads with root categories", async () => {
    // WHY: Markets MUST be green (#22C55E) and Tech cyan (#22D3EE); a depth-0 root
    // must sort before a depth-2 leaf so the colored category chips lead the row.
    const client = makeClient({
      user: { id: "user-1" },
      rows: [
        {
          interests: {
            interest_id: "i-chips",
            interest_label: "Chips & GPUs",
            depth_level: 2,
            interest_segment_slug: "tech",
          },
        },
        {
          interests: {
            interest_id: "i-mkt",
            interest_label: "Markets",
            depth_level: 0,
            interest_segment_slug: "markets",
          },
        },
      ],
    });

    const chips = await getUserInterests(client);

    // Root (Markets, depth 0) sorts ahead of the depth-2 leaf.
    expect(chips.map((chip) => chip.label)).toEqual(["Markets", "Chips & GPUs"]);
    expect(chips[0].accentHex).toBe("#22C55E"); // Markets → green
    expect(chips[1].accentHex).toBe("#22D3EE"); // Tech → cyan
  });

  it("returns a null accent for an interest carrying no segment (no fabricated color)", async () => {
    const client = makeClient({
      user: { id: "user-1" },
      rows: [
        {
          interests: {
            interest_id: "i-x",
            interest_label: "Custom topic",
            depth_level: 1,
            interest_segment_slug: null,
          },
        },
      ],
    });

    const chips = await getUserInterests(client);

    expect(chips).toHaveLength(1);
    expect(chips[0].accentHex).toBeNull();
  });

  it("returns [] when signed out without reading the owner-scoped table", async () => {
    const client = makeClient({ user: null });
    await expect(getUserInterests(client)).resolves.toEqual([]);
  });

  it("returns [] (never throws) when the read errors — the surface still paints", async () => {
    const client = makeClient({ user: { id: "user-1" }, error: { message: "rls denied" } });
    await expect(getUserInterests(client)).resolves.toEqual([]);
  });
});
