"use client";

/**
 * SourcesAddControls — the interactive add surfaces for the Sources · "What you
 * follow" screen ({@link SourcesScreen}):
 *
 *  - {@link AddSourceSearch}: a type-and-add bar across ALL four axes (YouTube,
 *    Podcast, X, Person). Two ways to add, neither a dead end:
 *      1. Live search (the three searchable axes) — typing queries the worker
 *         ({@link searchSources}) and tapping a result follows it (rich metadata).
 *      2. Type-and-add — pressing Enter (or tapping the "Add …" CTA) follows
 *         EXACTLY what was typed as a PENDING source via {@link upsertUserAddedSource}.
 *         This needs no worker / API key, so adding always works even when live
 *         search is unavailable, and it is the only path for the Person axis.
 *  - {@link AddInterestOverlay}: a full-screen overlay hosting the taxonomy
 *    {@link InterestChips} picker + a Save that persists picks to
 *    `user_interest_profile` via {@link persistInterestProfile}.
 *
 * Split out of SourcesScreen to keep that surface lean and these add flows
 * independently testable. Both call back (`onAdded` / `onSaved`) so the parent
 * re-reads its follow/interest lists after a successful write.
 */

import { useEffect, useRef, useState } from "react";
import { formatSubscriberCount } from "@/components/blip/reel/SettingsLayer";
import { InterestChips, type InterestSelection } from "@/components/onboarding/InterestChips";
import { logger } from "@/lib/logger";
import { persistInterestProfile } from "@/lib/onboardingProfile";
import { type SourceSearchResult, searchSources } from "@/lib/sourceSearch";
import { type UserAddedSourceInput, upsertUserAddedSource } from "@/lib/sources";
import { getCurrentSession } from "@/lib/supabase/auth";
import type { ContentSourceType } from "@/types/source";

/**
 * Per-axis chrome for the add bar: sprite glyph id + short toggle label, plus
 * whether the worker exposes a live search for this axis. `personality` (Person)
 * has no live external search, so it is `searchable: false` — it is added purely
 * by the type-and-add path below (the user types a name + Enter).
 */
const ADD_KINDS: { kind: ContentSourceType; glyph: string; label: string; searchable: boolean }[] = [
  { kind: "youtube_channel", glyph: "g-yt", label: "YouTube", searchable: true },
  { kind: "podcast", glyph: "g-pod", label: "Podcast", searchable: true },
  { kind: "x_account", glyph: "g-x", label: "X", searchable: true },
  { kind: "personality", glyph: "g-people", label: "Person", searchable: false },
];

/** Min query length before a search fires / a type-and-add is allowed (a single char is too noisy). */
const MIN_QUERY_LENGTH = 2;

/** Debounce window for the search input (mirrors the SP3a 300ms spec). */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Lower-cased, hyphen-collapsed slug of free text — the stable id stem for a
 * typed (pending) add, so re-typing the same name dedups onto one catalog row.
 */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Normalize a free-text X handle or profile URL to a bare, lower-cased handle —
 * `@Reuters`, `https://x.com/Reuters?lang=en`, `twitter.com/Reuters` all → `reuters`.
 * Mirrors the worker `x_resolver` convention so a typed add dedups with a searched one.
 */
function normalizeXHandle(raw: string): string {
  let handle = raw.trim();
  const urlMatch = handle.match(/(?:x\.com|twitter\.com)\/(@?[A-Za-z0-9_]+)/i);
  if (urlMatch) {
    handle = urlMatch[1];
  }
  return handle.replace(/^@+/, "").toLowerCase();
}

/**
 * Build a PENDING {@link UserAddedSourceInput} from exactly what the user typed,
 * for the type-and-add path. No live search / API key required — Phase 5d
 * enrichment later upgrades the pending row with real metadata. Returns `null`
 * when the input is too short / unusable (so the caller no-ops).
 *
 * `external_id` is deterministic per axis so the same input always resolves to one
 * catalog row: X → the normalized handle; the others → a `pending-{axis}:{slug}`
 * sentinel (distinct from real platform ids, which are channel ids / `itunes-*`).
 *
 * @param kind - The axis being added.
 * @param rawText - The raw text the user typed.
 * @returns The pending source input, or `null` when the text is unusable.
 */
function buildTypedSourceInput(kind: ContentSourceType, rawText: string): UserAddedSourceInput | null {
  const text = rawText.trim();
  if (text.length < MIN_QUERY_LENGTH) {
    return null;
  }
  if (kind === "x_account") {
    const handle = normalizeXHandle(text);
    if (!handle) {
      return null;
    }
    return {
      content_source_type: "x_account",
      external_id: handle,
      source_name: `@${handle}`,
      source_description: `@${handle}`,
      is_pending: true,
    };
  }
  const slug = slugify(text);
  if (!slug) {
    return null;
  }
  const prefix = kind === "youtube_channel" ? "pending-yt" : kind === "podcast" ? "pending-pod" : "pending-person";
  return {
    content_source_type: kind,
    external_id: `${prefix}:${slug}`,
    source_name: text,
    is_pending: true,
  };
}

/** A `<use>` sprite glyph. */
function Glyph({ id }: { id: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <use href={`#${id}`} />
    </svg>
  );
}

export interface AddSourceSearchProps {
  /** Fired after a result is successfully followed, so the parent re-reads its lists. */
  onAdded: () => void;
}

/**
 * Type-and-add bar across all four axes. Tapping opens a text input + axis toggle.
 * For the three searchable axes, typing (debounced) queries the worker and renders
 * results with an Add button each. On EVERY axis, pressing Enter — or tapping the
 * "Add …" CTA — follows exactly what was typed as a pending source, so adding never
 * dead-ends on a missing key / unreachable worker, and Person works at all.
 */
export function AddSourceSearch({ onAdded }: AddSourceSearchProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [kind, setKind] = useState<ContentSourceType>("youtube_channel");
  const [query, setQuery] = useState<string>("");
  // null = no search run yet; [] = ran and found nothing.
  const [results, setResults] = useState<SourceSearchResult[] | null>(null);
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [searchOk, setSearchOk] = useState<boolean>(true);
  const [addedExternalIds, setAddedExternalIds] = useState<Set<string>>(new Set());
  const [addingExternalId, setAddingExternalId] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  // Monotonic token so a slow in-flight search can never overwrite a newer one.
  const searchTokenRef = useRef<number>(0);

  const trimmedQuery = query.trim();
  const canTypedAdd = trimmedQuery.length >= MIN_QUERY_LENGTH && addingExternalId === null;
  const activeKind = ADD_KINDS.find((entry) => entry.kind === kind) ?? ADD_KINDS[0];

  // Debounced search on (query, kind). The Person axis has no live search; a
  // short/empty query (or switching to Person) clears results.
  useEffect(() => {
    const trimmed = query.trim();
    if (kind === "personality" || trimmed.length < MIN_QUERY_LENGTH) {
      setResults(null);
      setIsSearching(false);
      return;
    }
    // `kind` is now narrowed to a searchable axis (personality returned above).
    const searchKind = kind;
    setIsSearching(true);
    const token = searchTokenRef.current + 1;
    searchTokenRef.current = token;
    const timer = setTimeout(async () => {
      const outcome = await searchSources({ query: trimmed, kind: searchKind });
      // Stale guard: a newer keystroke/kind-switch superseded this request.
      if (searchTokenRef.current !== token) {
        return;
      }
      setResults(outcome.results);
      setSearchOk(outcome.search_ok);
      setIsSearching(false);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, kind]);

  /** Follow a search result (promote-to-catalog + owner follow), marking it added. */
  const handleAdd = async (result: SourceSearchResult): Promise<void> => {
    setAddingExternalId(result.external_id);
    setAddError(null);
    try {
      await upsertUserAddedSource({
        content_source_type: result.content_source_type,
        external_id: result.external_id,
        source_name: result.source_name,
        source_description: result.description,
        thumbnail_url: result.thumbnail_url,
        subscriber_count: result.subscriber_count,
        is_pending: result.is_pending,
      });
      setAddedExternalIds((prev) => new Set(prev).add(result.external_id));
      logger.info("sources_search_add_succeeded", {
        kind: result.content_source_type,
        external_id: result.external_id,
      });
      onAdded();
    } catch (error: unknown) {
      logger.error("sources_search_add_failed", {
        external_id: result.external_id,
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion: "Confirm the user is signed in; following a source is an authed action.",
      });
      setAddError("Couldn't add that — sign in and try again.");
    } finally {
      setAddingExternalId(null);
    }
  };

  /** Follow EXACTLY what the user typed as a pending source (no live search needed). */
  const handleAddTyped = async (): Promise<void> => {
    const input = buildTypedSourceInput(kind, query);
    if (!input || addingExternalId !== null) {
      return;
    }
    setAddingExternalId(input.external_id);
    setAddError(null);
    try {
      await upsertUserAddedSource(input);
      setAddedExternalIds((prev) => new Set(prev).add(input.external_id));
      logger.info("sources_typed_add_succeeded", {
        kind: input.content_source_type,
        external_id: input.external_id,
      });
      setQuery("");
      setResults(null);
      onAdded();
    } catch (error: unknown) {
      logger.error("sources_typed_add_failed", {
        kind: input.content_source_type,
        external_id: input.external_id,
        error_message: error instanceof Error ? error.message : "unknown",
        fix_suggestion: "Confirm the user is signed in; following a source is an authed action.",
      });
      setAddError("Couldn't add that — sign in and try again.");
    } finally {
      setAddingExternalId(null);
    }
  };

  /**
   * Enter-to-add: when fresh, addable search results exist, follow the top one
   * (rich metadata, dedup-correct); otherwise follow exactly what was typed.
   */
  const handleEnter = (): void => {
    if (!canTypedAdd) {
      return;
    }
    const topResult = (results ?? []).find(
      (result) => !addedExternalIds.has(result.external_id) && !result.is_already_added,
    );
    if (kind !== "personality" && topResult) {
      void handleAdd(topResult);
    } else {
      void handleAddTyped();
    }
  };

  if (!isOpen) {
    return (
      <button type="button" className="searchbar live" onClick={() => setIsOpen(true)}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <use href="#i-search" />
        </svg>
        <span>Add a YouTube channel, podcast, X account, or person…</span>
      </button>
    );
  }

  return (
    <div className="src-search">
      <div className="searchbar live">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <use href="#i-search" />
        </svg>
        <input
          type="text"
          value={query}
          placeholder={
            kind === "personality"
              ? "Type a person's name, then press Enter…"
              : `Search ${activeKind.label}, or type + Enter…`
          }
          // Reason: the bar was just tapped open — focus the input it became.
          // biome-ignore lint/a11y/noAutofocus: continuation of the user's tap gesture
          autoFocus
          onChange={(changeEvent) => setQuery(changeEvent.target.value)}
          onKeyDown={(keyEvent) => {
            if (keyEvent.key === "Enter") {
              keyEvent.preventDefault();
              handleEnter();
            }
          }}
        />
        <button
          type="button"
          className="src-search-close"
          aria-label="Close search"
          onClick={() => {
            setIsOpen(false);
            setQuery("");
            setResults(null);
          }}
        >
          Done
        </button>
      </div>

      <div className="src-kind-row">
        {ADD_KINDS.map((entry) => (
          <button
            type="button"
            key={entry.kind}
            className={`src-kind${kind === entry.kind ? " on" : ""}`}
            onClick={() => setKind(entry.kind)}
          >
            <Glyph id={entry.glyph} />
            {entry.label}
          </button>
        ))}
      </div>

      {addError !== null ? <p className="src-msg">{addError}</p> : null}

      {kind === "personality" ? (
        <p className="src-msg">Type a person's name and press Enter (or tap Add) to follow them.</p>
      ) : isSearching ? (
        <p className="src-msg">Searching…</p>
      ) : results === null ? (
        <p className="src-msg">
          Type a name to find channels, podcasts, or X accounts — or press Enter to add what you typed.
        </p>
      ) : !searchOk ? (
        <p className="src-msg">Live search is unavailable — press Enter to add “{trimmedQuery}” directly.</p>
      ) : results.length === 0 ? (
        <p className="src-msg">No matches — press Enter to add “{trimmedQuery}” directly.</p>
      ) : (
        <div className="src-results">
          {results.map((result) => {
            const isAdded = addedExternalIds.has(result.external_id) || result.is_already_added;
            const isAdding = addingExternalId === result.external_id;
            const subscriberLabel = formatSubscriberCount(result.subscriber_count);
            const glyph = ADD_KINDS.find((entry) => entry.kind === result.content_source_type)?.glyph ?? "g-yt";
            return (
              <div className="follow-row" key={result.external_id}>
                <div className="av sq">
                  {result.thumbnail_url ? (
                    // biome-ignore lint/performance/noImgElement: small remote avatar in a static export; next/image is inappropriate here.
                    <img src={result.thumbnail_url} alt="" />
                  ) : (
                    <span className="mono">{result.source_name.charAt(0).toUpperCase()}</span>
                  )}
                  <span className="pbadge">
                    <Glyph id={glyph} />
                  </span>
                </div>
                <div className="ft">
                  <div className="ftn">{result.source_name}</div>
                  <div className="fts">{subscriberLabel || result.description || "Tap add to follow"}</div>
                </div>
                <button
                  type="button"
                  className={`src-add-btn${isAdded ? " added" : ""}`}
                  disabled={isAdded || isAdding}
                  onClick={() => void handleAdd(result)}
                >
                  {isAdded ? "Added" : isAdding ? "Adding…" : "Add"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Always-available type-and-add: never a dead end, the only path for Person. */}
      {canTypedAdd ? (
        <button type="button" className="src-add-typed" onClick={() => void handleAddTyped()}>
          + Add “{trimmedQuery}” as {activeKind.kind === "x_account" ? "an" : "a"} {activeKind.label}
        </button>
      ) : null}
    </div>
  );
}

export interface AddInterestOverlayProps {
  /** Close the overlay without (or after) saving. */
  onClose: () => void;
  /** Fired after interests persist successfully, so the parent re-reads its chips. */
  onSaved: () => void;
}

/**
 * Full-screen overlay hosting the taxonomy interest picker + a Save that persists
 * the picks to `user_interest_profile`. Renders over the Sources surface (same
 * absolute-overlay pattern as the Build-your-30 editor in Settings).
 */
export function AddInterestOverlay({ onClose, onSaved }: AddInterestOverlayProps) {
  const [selection, setSelection] = useState<InterestSelection>({
    taxonomy_selections: [],
    custom_selections: [],
  });
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const pickCount = selection.taxonomy_selections.length + selection.custom_selections.length;

  /** Persist the picked interests for the signed-in user, then close on success. */
  const handleSave = async (): Promise<void> => {
    if (isSaving || pickCount === 0) {
      return;
    }
    setIsSaving(true);
    setError(null);
    const session = await getCurrentSession();
    const userId = session?.user?.id;
    if (!userId) {
      setError("Sign in to add interests.");
      setIsSaving(false);
      return;
    }
    try {
      const result = await persistInterestProfile(userId, selection, { profile_source: "typed" });
      logger.info("sources_add_interests_saved", {
        persisted: result.persisted_count,
        unpersisted: result.unpersisted_customs.length,
      });
      onSaved();
      onClose();
    } catch (saveError: unknown) {
      logger.error("sources_add_interests_failed", {
        error_message: saveError instanceof Error ? saveError.message : "unknown",
        fix_suggestion: "Confirm the user is authed and user_interest_profile owner-all RLS permits the write.",
      });
      setError("Couldn't save your interests. Try again.");
      setIsSaving(false);
    }
  };

  return (
    <div className="add-overlay">
      <div className="add-overlay-head">
        <span className="t">Add interests</span>
        <button type="button" onClick={onClose} disabled={isSaving}>
          Cancel
        </button>
      </div>
      <div className="add-overlay-body">
        <InterestChips onSelectionChange={setSelection} />
        {error !== null ? <p className="add-err">{error}</p> : null}
      </div>
      <div className="add-overlay-foot">
        <button type="button" onClick={() => void handleSave()} disabled={isSaving || pickCount === 0}>
          {isSaving ? "Saving…" : pickCount === 0 ? "Pick at least one" : `Add ${pickCount}`}
        </button>
      </div>
    </div>
  );
}
