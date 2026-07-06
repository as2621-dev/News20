/**
 * Tiny immutable Set helpers for React setState updaters (the "clone, add-or-delete, return"
 * pattern the multi-select pickers all use). Pure — a new Set out, the input untouched.
 */

/**
 * Return a NEW Set with `value` toggled: removed if present, added if absent. The input is
 * not mutated, so it is safe as a React `setState((prev) => toggleInSet(prev, value))` updater.
 *
 * @param set - The current set.
 * @param value - The value to flip.
 * @returns A new set with `value` toggled.
 *
 * @example
 * setSelected((prev) => toggleInSet(prev, "ai"));
 */
export function toggleInSet<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}
