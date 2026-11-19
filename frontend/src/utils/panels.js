// Side panel widths (C6). A width saved on a large monitor, or a corrupt
// one, must not leave a projector with no answer column: saved widths are
// read within their limits, and the open panels share what is left after the
// centre column's minimum.

export const RIBBON_WIDTH = 56
export const CENTER_MIN_WIDTH = 480

/** A saved width within [min, max]; `fallback` when missing or not a number. */
export function readWidth(raw, fallback, min, max) {
  const n = parseInt(raw || '', 10)
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback
}

/** The most each of `openPanels` panels may take on a `viewport`-wide window. */
export function panelBudget(viewport, openPanels) {
  return openPanels ? Math.floor((viewport - RIBBON_WIDTH - CENTER_MIN_WIDTH) / openPanels) : Infinity
}

/** A panel's width on screen: its own, within its minimum and the budget. */
export function fitPanel(width, min, budget) {
  return Math.max(min, Math.min(width, budget))
}
