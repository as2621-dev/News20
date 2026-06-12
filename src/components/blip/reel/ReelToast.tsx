"use client";

/**
 * ReelToast — a small bottom-center confirmation pill over the reel (e.g.
 * "Following this story" after the follow button). Pure presentational: the
 * parent owns the message state and the auto-dismiss timer; this component just
 * fades via the `.on` class so the element can transition out while keeping its
 * last message visible.
 *
 * @example
 * <ReelToast message={toastMessage} />
 */

import { useEffect, useState } from "react";

export interface ReelToastProps {
  /** The toast text, or null when hidden. */
  message: string | null;
}

/**
 * Render the toast pill. Keeps the LAST non-null message during the fade-out so
 * the text doesn't blank mid-transition.
 */
export function ReelToast({ message }: ReelToastProps) {
  const [lastMessage, setLastMessage] = useState<string>("");

  useEffect(() => {
    if (message !== null) {
      setLastMessage(message);
    }
  }, [message]);

  return (
    <div className={`reel-toast${message !== null ? " on" : ""}`} role="status" aria-live="polite">
      {message ?? lastMessage}
    </div>
  );
}
