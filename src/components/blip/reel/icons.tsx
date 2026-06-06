/**
 * `ic()` — render one glyph from the shared {@link BlipIconDefs} sprite as
 * `<svg class="ico"><use href="#i-<name>"/></svg>`, the prototype `blip-reel.js`
 * `ic(name)` helper ported to JSX. The `.ico` size + the `#i-*` symbols both live
 * in the vendored Blip CSS / `BlipIconDefs`, so every Stage-4 reel glyph is
 * styled and resolved for free. Render `<BlipIconDefs/>` once above any consumer.
 */
import type { ReactElement } from "react";

/** The icon ids carried by {@link BlipIconDefs} (`#i-<name>`). */
export type BlipIconName =
  | "arrow"
  | "back"
  | "check"
  | "close"
  | "doc"
  | "following"
  | "keyboard"
  | "pause"
  | "play"
  | "plus"
  | "profile"
  | "save"
  | "search"
  | "send"
  | "share"
  | "spark"
  | "undo"
  | "voice"
  | "x";

/**
 * Render a sprite icon by name (prototype `ic()`).
 *
 * @param name - The `#i-<name>` symbol id to reference.
 * @returns The `<svg class="ico">` element wrapping a `<use>` of that symbol.
 *
 * @example
 * <button className="play-btn">{ic("pause")}</button>
 */
export function ic(name: BlipIconName): ReactElement {
  return (
    <svg className="ico" viewBox="0 0 24 24" aria-hidden="true">
      <use href={`#i-${name}`} />
    </svg>
  );
}
