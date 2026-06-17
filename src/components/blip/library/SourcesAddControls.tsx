"use client";

/**
 * SourcesAddControls — the interactive add surfaces for the Sources · "What you
 * follow" screen ({@link SourcesScreen}):
 *
 *  - {@link AddSourceSearch}: a live search-and-add bar over the worker search
 *    endpoint ({@link searchSources}) for the three searchable axes (YouTube,
 *    Podcast, X). Tapping a result follows it via {@link upsertUserAddedSource}.
 *    "Person" (personality) is a curated catalog axis with no live search, so it
 *    is intentionally NOT offered here (added via onboarding only).
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
import { type SearchableSourceType, type SourceSearchResult, searchSources } from "@/lib/sourceSearch";
import { upsertUserAddedSource } from "@/lib/sources";
import { getCurrentSession } from "@/lib/supabase/auth";

/** Per-searchable-axis chrome: sprite glyph id + short toggle label. */
const SEARCH_KINDS: { kind: SearchableSourceType; glyph: string; label: string }[] = [
  { kind: "youtube_channel", glyph: "g-yt", label: "YouTube" },
  { kind: "podcast", glyph: "g-pod", label: "Podcast" },
  { kind: "x_account", glyph: "g-x", label: "X" },
];

/** Min query length before a search fires (a single char is too noisy). */
const MIN_QUERY_LENGTH = 2;

/** Debounce window for the search input (mirrors the SP3a 300ms spec). */
const SEARCH_DEBOUNCE_MS = 300;

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
 * Live search-and-add bar. Tapping opens a text input + axis toggle; typing
 * (debounced) queries the worker and renders results with an Add button each.
 */
export function AddSourceSearch({ onAdded }: AddSourceSearchProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [kind, setKind] = useState<SearchableSourceType>("youtube_channel");
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

  // Debounced search on (query, kind). A short/empty query clears results.
  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      setResults(null);
      setIsSearching(false);
      return;
    }
    setIsSearching(true);
    const token = searchTokenRef.current + 1;
    searchTokenRef.current = token;
    const timer = setTimeout(async () => {
      const outcome = await searchSources({ query: trimmed, kind });
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

  if (!isOpen) {
    return (
      <button type="button" className="searchbar live" onClick={() => setIsOpen(true)}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <use href="#i-search" />
        </svg>
        <span>Add a channel, person, or topic…</span>
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
          placeholder="Search YouTube, podcasts, or X…"
          // Reason: the bar was just tapped open — focus the input it became.
          // biome-ignore lint/a11y/noAutofocus: continuation of the user's tap gesture
          autoFocus
          onChange={(changeEvent) => setQuery(changeEvent.target.value)}
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
        {SEARCH_KINDS.map((entry) => (
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

      {isSearching ? (
        <p className="src-msg">Searching…</p>
      ) : results === null ? (
        <p className="src-msg">Type a name to find channels, podcasts, or X accounts to follow.</p>
      ) : !searchOk ? (
        <p className="src-msg">Search is unavailable right now. Try again in a moment.</p>
      ) : results.length === 0 ? (
        <p className="src-msg">No matches. Try a different spelling.</p>
      ) : (
        <div className="src-results">
          {results.map((result) => {
            const isAdded = addedExternalIds.has(result.external_id) || result.is_already_added;
            const isAdding = addingExternalId === result.external_id;
            const subscriberLabel = formatSubscriberCount(result.subscriber_count);
            const glyph = SEARCH_KINDS.find((entry) => entry.kind === result.content_source_type)?.glyph ?? "g-yt";
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
