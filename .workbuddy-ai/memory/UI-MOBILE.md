# UI / mobile — detail for the index

Split out of `MEMORY.md` 2026-09-24: the index is injected every session and hard-truncated at
~6,520 bytes, so this section was being dropped from the tail. Read this file when touching page
sheets, `@media` breakpoints, or the mobile dock.

## UI / mobile

* **Check which stylesheet a page loads before believing a fix landed.** Only `dashboard.html` loads
  `ios_mobile.css`; `/` and `/dashboard` serve it (`index.html` is `/classic`), so `/` *looks* fixed while
  `/stocks /india /options /console` run their own sheets + `ios_pages.css`. A page sheet's `!important`
  beats `hm_ui.css`'s specificity. Visually-hidden text has 3 class names (`.tt-sr-only`, `.sr-only`,
  `.cx-visually-hidden`).
* **Prove `@media (max-width: 1024px)` leaves desktop untouched** — 1440px snapshot, `sheet.disabled = true`,
  re-snapshot, diff, in one page load (`.scratch/prove_desktop.js`). Fix at the source with `:not()`.
* **A server-rendered control whose handler is defined by a later blocking script is dead on arrival.** The
  dock's inline `onclick` resolved at *click* time but the handler lived ~400 lines later, so early taps
  threw and did nothing — reading as **intermittent** (warm cache passes; ~1 run in 3 fails). Fixed by
  loading `mobile_dock.js` **before** the dock. Delegated listeners were never affected.
* **A page in `PAGES` is not a page that is measured** — the verifier's market pages ran 3 checks and its
  `GLASS_SELECTORS` were dashboard-only. **Prove a fix is non-vacuous by reverting it.** 238/238 → 321/321.
* **Grep for the write, not just the read** — `state.activeMobileView` was read by the resize handler and all
  three inits, written by only one, silently reverting user state.
* **`getComputedStyle` reports an animation on a `display:none` element** — filter by
  `getBoundingClientRect().width > 0`. Detail for this whole section: `2026-09-22.md`.
