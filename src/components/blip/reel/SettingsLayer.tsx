"use client";

/**
 * SettingsLayer — the full-screen Settings surface from the "App Surfaces —
 * Settings, Archive, Sources" design board. It is the "Settings" tab of the
 * 4-tab library ({@link AppShell}); the blip wordmark opens the library here.
 * (`onClose` is optional — only the legacy overlay mount renders a `← REEL`
 * back button; as a tab, navigation is the bottom tab bar.)
 *
 * Sections:
 *   - Account header — avatar + display name + signed-in email (real session)
 *   - ACCOUNT — editable name, email, delete-account row (danger)
 *   - SUBSCRIPTION — Free→Pro plan card with the cream Upgrade CTA
 *   - Sign out + app version footer
 *
 * (The "Build your 30" allocation editor moved out to its own "Thirty" library tab —
 * see {@link AppShell}.)
 *
 * **Stubbed honestly.** Payments and account deletion are not built yet, so
 * Upgrade / Delete taps surface an inline "not yet" note instead of pretending.
 *
 * @example
 * <SettingsLayer />            // as the library Settings tab
 * <SettingsLayer onClose={closeOverlay} />  // legacy overlay mount
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ic } from "@/components/blip/reel/icons";
import { logger } from "@/lib/logger";
import { getProfileDisplayName, PROFILE_DISPLAY_NAME_MAX_LENGTH, saveProfileDisplayName } from "@/lib/profile";
import { getCurrentSession, signOut } from "@/lib/supabase/auth";
import packageJson from "../../../../package.json";

/**
 * Format a subscriber/follower count compactly (e.g. 1200 → "1.2K", 3_400_000 →
 * "3.4M"). Returns an empty string for null/zero so the meta line stays clean.
 */
export function formatSubscriberCount(count: number | null): string {
  if (!count || count <= 0) {
    return "";
  }
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(1).replace(/\.0$/, "")}M followers`;
  }
  if (count >= 1_000) {
    return `${(count / 1_000).toFixed(1).replace(/\.0$/, "")}K followers`;
  }
  return `${count} followers`;
}

export interface SettingsLayerProps {
  /**
   * Close the settings surface and return to the reel. Optional: when Settings is
   * a library TAB ({@link AppShell}), navigation is the bottom tab bar, so no
   * `← REEL` back button is rendered. Provided only in the legacy overlay mount.
   */
  onClose?: () => void;
}

/** Which stubbed action's inline "not yet" note is showing, if any. */
type StubNote = "upgrade" | "delete" | null;

/**
 * Derive a display name from the signed-in email's local part — the app's email
 * OTP auth carries no profile-name field, so "riya.sharma14" → "Riya Sharma".
 *
 * @param email - The signed-in email address.
 * @returns Title-cased words from the local part, digits stripped.
 *
 * @example
 * deriveDisplayNameFromEmail("riya.sharma14@example.com") // "Riya Sharma"
 */
export function deriveDisplayNameFromEmail(email: string): string {
  const localPart = email.split("@")[0] ?? "";
  const words = localPart
    .split(/[._\-+]/)
    .map((word) => word.replace(/\d+/g, ""))
    .filter((word) => word.length > 0);
  if (words.length === 0) {
    return email;
  }
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

/**
 * Render the settings layer body: account header + rows, subscription card, and
 * sign out. Sign-out routes to `/onboarding` via `router.replace` (same target as
 * the {@link AppRouter} gate).
 */
export function SettingsLayer({ onClose }: SettingsLayerProps) {
  const router = useRouter();
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null);
  const [isSigningOut, setIsSigningOut] = useState<boolean>(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const [stubNote, setStubNote] = useState<StubNote>(null);
  // The saved profile name (migration 0012) — null until loaded / when unset,
  // in which case the email-derived fallback renders.
  const [profileName, setProfileName] = useState<string | null>(null);
  const [isEditingName, setIsEditingName] = useState<boolean>(false);
  const [nameDraft, setNameDraft] = useState<string>("");
  const [isSavingName, setIsSavingName] = useState<boolean>(false);
  const [nameEditError, setNameEditError] = useState<string | null>(null);

  // Resolve the signed-in email + saved profile name once on mount (layer
  // mounts only while open).
  useEffect(() => {
    let isMounted = true;
    getCurrentSession()
      .then((session) => {
        if (isMounted) {
          setSignedInEmail(session?.user?.email ?? null);
        }
      })
      .catch((sessionError: unknown) => {
        logger.error("settings_session_read_failed", {
          error_message: sessionError instanceof Error ? sessionError.message : "unknown",
          fix_suggestion: "Check the NEXT_PUBLIC_SUPABASE_* env vars; settings shows a signed-out state.",
        });
      });
    getProfileDisplayName()
      .then((savedDisplayName) => {
        if (isMounted && savedDisplayName !== null) {
          setProfileName(savedDisplayName);
        }
      })
      .catch((profileError: unknown) => {
        logger.error("settings_profile_read_failed", {
          error_message: profileError instanceof Error ? profileError.message : "unknown",
          fix_suggestion: "Confirm migration 0012 applied; settings falls back to the email-derived name.",
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
      logger.info("settings_signed_out", {});
      router.replace("/onboarding");
      return;
    }
    setIsSigningOut(false);
    setSignOutError(result.error_message);
  };

  /** Toggle the inline "not yet" note for a stubbed action (Upgrade / Delete). */
  const toggleStubNote = (note: Exclude<StubNote, null>): void => {
    logger.info("settings_stub_action_tapped", { action: note });
    setStubNote((current) => (current === note ? null : note));
  };

  const displayName =
    profileName ?? (signedInEmail !== null ? deriveDisplayNameFromEmail(signedInEmail) : "Not signed in");
  const avatarInitial = displayName.charAt(0).toUpperCase() || "B";

  /** Open the inline name editor seeded with the current display name. */
  const startNameEdit = (): void => {
    setNameDraft(profileName ?? (signedInEmail !== null ? deriveDisplayNameFromEmail(signedInEmail) : ""));
    setNameEditError(null);
    setIsEditingName(true);
  };

  /** Persist the drafted name; on success update the header + close the editor. */
  const handleNameSave = async (): Promise<void> => {
    if (isSavingName) {
      return;
    }
    setIsSavingName(true);
    setNameEditError(null);
    const result = await saveProfileDisplayName(nameDraft);
    setIsSavingName(false);
    if (result.ok) {
      setProfileName(nameDraft.trim());
      setIsEditingName(false);
      return;
    }
    setNameEditError(result.error_message);
  };

  return (
    <>
      <div className="art-top">
        {onClose ? (
          <button type="button" className="v-back" onClick={onClose}>
            {ic("back")} REEL
          </button>
        ) : null}
      </div>

      <div className="art-scroll">
        <div className="set-kicker">Account</div>
        <h1 className="set-htitle">Settings</h1>

        <div className="set-acct">
          <div className="set-av">
            <span>{avatarInitial}</span>
          </div>
          <div>
            <div className="set-acct-name">{displayName}</div>
            <div className="set-acct-email">{signedInEmail ?? "Sign in to sync your briefing"}</div>
          </div>
        </div>

        <div className="set-seclabel">Account</div>
        {isEditingName ? (
          <div className="set-name-edit">
            <input
              type="text"
              value={nameDraft}
              maxLength={PROFILE_DISPLAY_NAME_MAX_LENGTH}
              placeholder="Your name"
              // Reason: the row was just tapped — focus the editor it turned into.
              // biome-ignore lint/a11y/noAutofocus: continuation of the user's tap gesture
              autoFocus
              onChange={(changeEvent) => setNameDraft(changeEvent.target.value)}
              onKeyDown={(keyEvent) => {
                if (keyEvent.key === "Enter") {
                  void handleNameSave();
                }
              }}
            />
            <button type="button" className="save" onClick={() => void handleNameSave()} disabled={isSavingName}>
              {isSavingName ? "Saving…" : "Save"}
            </button>
            <button type="button" className="cancel" onClick={() => setIsEditingName(false)} disabled={isSavingName}>
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" className="set-row first" onClick={startNameEdit}>
            <div className="set-rmain">
              <div className="set-rlabel">Name</div>
            </div>
            <div className="set-rval">
              {displayName}
              <svg className="set-chev" viewBox="0 0 24 24" aria-hidden="true">
                <use href="#i-chev" />
              </svg>
            </div>
          </button>
        )}
        {nameEditError !== null ? <p className="set-stubnote">{nameEditError}</p> : null}
        <div className="set-row" style={{ cursor: "default" }}>
          <div className="set-rmain">
            <div className="set-rlabel">Email</div>
          </div>
          <div className="set-rval">
            <span className="set-mono">{signedInEmail ?? "—"}</span>
          </div>
        </div>

        <div className="set-seclabel">Subscription</div>
        <p className="set-subnote">You're on the free plan.</p>
        <div className="set-plan-card">
          <div className="set-plan-tier">
            <div>
              <div className="set-ptag">Current plan</div>
              <div className="set-pname">Free</div>
            </div>
            <div className="set-pmeta">
              15 sources
              <br />
              30-min reel
            </div>
          </div>
          <div className="set-plan-tier set-plan-pro">
            <div>
              <div className="set-ptag">Pro</div>
              <div className="set-pname">
                $9 <span className="set-pper">/ month</span>
              </div>
            </div>
            <div className="set-pmeta">coming soon</div>
          </div>
          <button type="button" className="set-upgrade" onClick={() => toggleStubNote("upgrade")}>
            Update to Pro
          </button>
        </div>
        {stubNote === "upgrade" ? <p className="set-stubnote">Pro is coming soon.</p> : null}

        <div className="set-signout">
          <button type="button" onClick={handleSignOut} disabled={isSigningOut}>
            {isSigningOut ? "Signing out…" : "Sign out"}
          </button>
          <span className="set-ver">blip {packageJson.version}</span>
        </div>
        {signOutError !== null ? <p className="set-stubnote">{signOutError}</p> : null}

        {/* Destructive action kept at the very bottom (below Sign out) so it's never the
            first thing a user reaches in Settings. */}
        <div className="set-seclabel">Danger zone</div>
        <button type="button" className="set-row first" onClick={() => toggleStubNote("delete")}>
          <div className="set-rmain">
            <div className="set-rlabel set-danger">Delete account</div>
            <div className="set-rsub">Your briefings and memory, gone for good</div>
          </div>
          <svg className="set-chev set-danger" viewBox="0 0 24 24" aria-hidden="true">
            <use href="#i-chev" />
          </svg>
        </button>
        {stubNote === "delete" ? (
          <p className="set-stubnote">Account deletion isn't self-serve yet — email support and we'll handle it.</p>
        ) : null}
      </div>
    </>
  );
}
