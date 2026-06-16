import { describe, expect, it, vi } from "vitest";
import { getFollowedSourcesWithPriority } from "@/lib/sources";
import type { ContentSource, UserContentSource } from "@/types/source";

/**
 * getFollowedSourcesWithPriority — the read backing the Sources surface's
 * channels/people list with active/paused toggles.
 *
 * WHY these tests (Rule 9): UNLIKE getFollowedSources, this MUST keep `off`
 * (paused) follows — the toggle exists precisely to flip a source between active
 * and paused, so a paused source has to appear (with its priority) or the user
 * could never un-pause it. We assert a paused follow survives the read and that
 * every returned source carries the correct priority joined from the junction.
 *
 * Mocks Supabase at the client boundary (CLAUDE.md mocking strategy).
 */

const AUTHED_USER_ID = "user-1";

function makeSource(id: string, name: string): ContentSource {
  return {
    source_id: id,
    content_source_type: "youtube_channel",
    external_id: `ext-${id}`,
    source_name: name,
    source_description: null,
    thumbnail_url: null,
    subscriber_count: null,
    platform_metadata: null,
    personas: [],
    topic_tags: [],
    popularity_score: 50,
    is_curated: true,
    last_fetched_at: null,
  };
}

/**
 * A fake client serving the two reads:
 *  - from("user_content_sources").select().eq().returns() → the follow junction
 *  - from("content_sources").select().in().order().returns() → the catalog rows
 * plus auth.getUser (requireAuthedUserId).
 */
function makeClient(options: { follows: UserContentSource[]; sources: ContentSource[] }) {
  function from(table: string) {
    const result =
      table === "user_content_sources"
        ? { data: options.follows, error: null }
        : { data: options.sources, error: null };
    const builder = {
      select: () => builder,
      eq: () => builder,
      in: () => builder,
      order: () => builder,
      returns: vi.fn().mockResolvedValue(result),
    };
    return builder;
  }
  return {
    auth: { getUser: vi.fn().mockResolvedValue({ data: { user: { id: AUTHED_USER_ID } }, error: null }) },
    from,
  } as never;
}

describe("getFollowedSourcesWithPriority (keeps paused follows + attaches priority)", () => {
  it("includes a paused (off) follow and tags each source with its source_priority", async () => {
    const client = makeClient({
      follows: [
        { user_id: AUTHED_USER_ID, source_id: "s-active", source_priority: "everything", added_via: null },
        { user_id: AUTHED_USER_ID, source_id: "s-paused", source_priority: "off", added_via: null },
      ],
      sources: [makeSource("s-active", "Active Channel"), makeSource("s-paused", "Paused Channel")],
    });

    const result = await getFollowedSourcesWithPriority(client);

    const byId = new Map(result.map((source) => [source.source_id, source]));
    expect(byId.get("s-active")?.source_priority).toBe("everything");
    // The whole point: the paused follow is RETAINED (getFollowedSources would drop it).
    expect(byId.get("s-paused")?.source_priority).toBe("off");
  });

  it("returns [] for a user with no follows (no catalog read attempted)", async () => {
    const client = makeClient({ follows: [], sources: [] });
    await expect(getFollowedSourcesWithPriority(client)).resolves.toEqual([]);
  });
});
