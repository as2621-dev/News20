"use client";

/**
 * AccountSheet — the minimal account bottom sheet opened by tapping the blip
 * wordmark on the reel. Mirrors the {@link AskSheet} sheet anatomy (`sheet-grab`
 * + `ask-head` + body) so it inherits the dark Blip system styling from
 * `blip-flow.css` unchanged; the sliding `.sheet` container + scrim are owned by
 * {@link BlipReel}.
 *
 * Contents: the signed-in email (from {@link getCurrentSession}), a Sign out
 * action ({@link signOut} → route back to `/onboarding`), and the app version.
 *
 * @example
 * <AccountSheet onClose={closeOverlay} />
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ic } from "@/components/blip/reel/icons";
import { logger } from "@/lib/logger";
import { getCurrentSession, signOut } from "@/lib/supabase/auth";
import packageJson from "../../../../package.json";

export interface AccountSheetProps {
  /** Close the sheet and return to the reel. */
  onClose: () => void;
}

/**
 * Render the account sheet body: signed-in email, sign out, app version.
 *
 * Sign-out routes to `/onboarding` via `router.replace` (the same target the
 * {@link AppRouter} root gate uses), so the splash mounts without the gated `/`
 * staying a back target.
 */
export function AccountSheet({ onClose }: AccountSheetProps) {
  const router = useRouter();
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null);
  const [isSigningOut, setIsSigningOut] = useState<boolean>(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  // Resolve the signed-in email once on mount (sheet mounts only while open).
  useEffect(() => {
    let isMounted = true;
    getCurrentSession()
      .then((session) => {
        if (isMounted) {
          setSignedInEmail(session?.user?.email ?? null);
        }
      })
      .catch((sessionError: unknown) => {
        logger.error("account_sheet_session_read_failed", {
          error_message: sessionError instanceof Error ? sessionError.message : "unknown",
          fix_suggestion: "Check the NEXT_PUBLIC_SUPABASE_* env vars; the sheet shows a signed-out state.",
        });
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const handleSignOut = async (): Promise<void> => {
    if (isSigningOut) {
      return;
    }
    setIsSigningOut(true);
    setSignOutError(null);
    const result = await signOut();
    if (result.ok) {
      logger.info("account_sheet_signed_out", {});
      router.replace("/onboarding");
      return;
    }
    setIsSigningOut(false);
    setSignOutError(result.error_message);
  };

  return (
    <>
      <div className="sheet-grab" />
      <div className="ask-head">
        <div className="ah-title">
          <span className="seg-dot" />
          <span className="ah-text">Account</span>
        </div>
        <button type="button" className="sheet-x" aria-label="Close" onClick={onClose}>
          {ic("close")}
        </button>
      </div>
      <div className="sheet-body" style={{ alignItems: "stretch", gap: 18, padding: "8px 22px 22px" }}>
        <div>
          <div
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              color: "rgba(255,255,255,0.4)",
            }}
          >
            Signed in as
          </div>
          <div style={{ marginTop: 6, fontSize: 15, color: "rgba(255,255,255,0.88)" }}>
            {signedInEmail ?? "Not signed in"}
          </div>
        </div>

        <button type="button" className="v-btn solid" onClick={handleSignOut} disabled={isSigningOut}>
          {isSigningOut ? "Signing out…" : "Sign out"}
        </button>
        {signOutError !== null ? (
          <p style={{ color: "rgba(255,120,120,0.85)", fontSize: 12.5, lineHeight: 1.45, margin: 0 }}>{signOutError}</p>
        ) : null}

        <div
          style={{
            marginTop: "auto",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.3)",
            textAlign: "center",
          }}
        >
          blip · v{packageJson.version}
        </div>
      </div>
    </>
  );
}
