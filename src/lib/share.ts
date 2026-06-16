/**
 * Story sharing for the reel's right-rail Share button.
 *
 * One entry point, {@link shareStory}, that opens the native share sheet via
 * `@capacitor/share` — on iOS that is the system sheet; on the web the plugin
 * wraps `navigator.share`. Where neither is available (desktop dev browsers),
 * it degrades to copying the link/headline to the clipboard so the button is
 * never a dead end. User-cancelling the sheet is a normal outcome, not an
 * error.
 */

import { Share } from "@capacitor/share";
import { logger } from "@/lib/logger";

/** What a {@link shareStory} call actually did (drives the caller's toast). */
export type ShareOutcome = "shared" | "copied" | "unavailable";

/** The share payload: the story headline plus its best source article link. */
export interface ShareStoryPayload {
  /** The story headline (`Story.headline`). */
  headline: string;
  /** The primary source article URL, or `null` to share headline-only. */
  articleUrl: string | null;
}

/**
 * True when the throw from `Share.share()` means the user dismissed the sheet.
 *
 * Reason: Capacitor rejects with "Share canceled" on iOS and the web layer
 * rejects with an `AbortError` — both are normal user choices, not failures.
 */
function isUserCancellation(error: unknown): boolean {
  if (error instanceof Error) {
    return error.name === "AbortError" || /cancel/i.test(error.message);
  }
  return false;
}

/**
 * Share a story via the native share sheet, with a clipboard fallback.
 *
 * @param payload - The headline + optional source article URL to share.
 * @returns `"shared"` when the sheet opened (including user-cancel), `"copied"`
 *   when the clipboard fallback was used, `"unavailable"` when neither worked.
 *
 * @example
 * const outcome = await shareStory({ headline: story.headline, articleUrl });
 * if (outcome === "copied") showToast("Link copied");
 */
export async function shareStory(payload: ShareStoryPayload): Promise<ShareOutcome> {
  const { headline, articleUrl } = payload;
  logger.info("reel_share_started", { headline, has_article_url: articleUrl !== null });
  try {
    await Share.share({
      title: headline,
      // Reason: with a URL the headline goes in `title`/`text` and the link in
      // `url`; without one the headline IS the shared content.
      text: headline,
      url: articleUrl ?? undefined,
      dialogTitle: headline,
    });
    logger.info("reel_share_completed", { headline });
    return "shared";
  } catch (error) {
    if (isUserCancellation(error)) {
      logger.info("reel_share_cancelled", { headline });
      return "shared";
    }
    // Desktop dev fallback: copy the link (or headline) so the tap still helps.
    try {
      await navigator.clipboard.writeText(articleUrl ?? headline);
      logger.info("reel_share_copied_to_clipboard", { headline });
      return "copied";
    } catch (clipboardError) {
      logger.error("reel_share_failed", {
        headline,
        error_message: error instanceof Error ? error.message : "Unknown",
        clipboard_error_message: clipboardError instanceof Error ? clipboardError.message : "Unknown",
        fix_suggestion: "Confirm @capacitor/share is synced into the iOS project (npx cap sync ios).",
      });
      return "unavailable";
    }
  }
}
