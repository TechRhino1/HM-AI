# HM Algo 2.0 — UI Design System & Modernisation

**Status:** delivered (additive layer, no functional regressions)
**Scope owner:** HM Algo 2.0 web terminal
**Artefacts:** `jarvis/ui/static/css/hm_ui.css`, `jarvis/ui/static/js/hm_ui.js`, `docs/ui_preview.html`

---

## 1. Scope of the changes

### 1.1 What this change *is*

A **non-breaking design-system layer** applied on top of the four existing UIs. It modernises
the visual language and the interaction/accessibility baseline without rewriting the page
stylesheets or scripts.

| Area | Change |
|---|---|
| **Tokens** | One unified `--hm-*` vocabulary (colour, type, space, radius, elevation, motion) replaces four divergent `:root` sets as the source of truth. |
| **Colour** | Six-step surface ladder; `--text-dim` corrected from `#64748b` to `#8494ab` to clear WCAG AA; the two conflicting accent cyans unified to `#38bdf8`. |
| **Typography** | Named scale (`--hm-text-2xs` … `--hm-text-2xl`) with a 12 px floor for sentence-case text, replacing ~155 ad-hoc pixel sizes. |
| **Spacing** | Single 4 px grid (`--hm-space-1` … `--hm-space-7`). |
| **Layout** | Shared shell rules for HUD, nav, mobile tab bar, cards, tables, modals. |
| **Usability** | Cross-page nav state derived from the URL; consistent hover/active/disabled semantics; non-blocking toast feedback replacing 10 blocking `alert()` calls. |
| **Responsiveness** | Consolidated breakpoints (1280 / 1024 / 900 / 600 / 480) plus landscape-phone and coarse-pointer handling; safe-area insets for notched devices. |
| **Accessibility** | Focus ring restored, skip links, landmarks, ARIA tab/dialog semantics, modal focus trap, `aria-live` region, 44 px touch targets, reduced-motion and increased-contrast support. |

### 1.2 What this change explicitly is **not**

- **Not** a rewrite of `terminal.css`, `stocks.css`, `india.css`, `india_options.css`. They are
  large, id-entangled and currently correct; replacing them would be a separate, visually
  regression-tested project.
- **Not** a change to any Python code, API, route, data flow or business logic.
- **Not** a change to any element id, class name, inline handler or DOM structure that the page
  scripts depend on. Verified mechanically — see §5.
- **Not** a font-size uplift for the dense numeric readouts. This is a deliberate trade-off; see
  §4.1.

### 1.3 Non-goals / deferred

| Deferred item | Why | Recommended follow-up |
|---|---|---|
| Deleting the four page `:root` blocks | Each page sheet must remain usable standalone if `hm_ui.css` fails to load. The values are overridden, not conflicting, so there is no rendering bug to fix. | Migrate page-by-page to `--hm-*`, then delete. Mapping in §3. |
| Merging the four page stylesheets into one | Would require visual regression testing across every panel; high risk, low immediate user benefit. | After the token migration above. |
| Removing the 8 `outline: none` declarations at source | Functionally superseded by the `!important` focus rule; editing 6 files adds churn for no visual gain. | Clean up opportunistically when each sheet is next touched. |
| `app.html` / `app_shell.css` / `app_shell.js` | Orphaned: no route served them (see §6.1). | **Done** — deleted in the console redesign (`5bfab68`). |

---

## 2. Intended target users

The four UIs share one user population with four task modes. The design decisions were made
against these personas, in priority order.

### 2.1 Primary — the active intraday trader (desktop, high frequency)
Uses the terminal (`index.html`) continuously for hours. Needs maximum information density,
low-latency scanning, and unambiguous state (mode, position, P&L).
**Design implication:** density is preserved. This is the reason small numeric text was *not*
enlarged — instead its contrast was raised, which improves readability without breaking the
fixed-height grid rows. Dark surfaces stay dark; no light-mode variant was introduced.

### 2.2 Primary — the mobile trader (phone, on the move)
Checks positions and fires actions one-handed, often on a poor connection, sometimes on a
notched device in landscape.
**Design implication:** the mobile tab bar is the primary navigation, so every tab became a
44 px target (previously ~28 px), got `role="tab"` semantics and arrow-key support; toast
feedback replaces modal `alert()` because a blocking dialog on a phone is genuinely hostile;
`safe-area-inset` padding prevents the home indicator overlapping controls; landscape-phone
rules reclaim vertical space.

### 2.3 Secondary — the equity / India-markets analyst
Works the screener and heatmap surfaces (`stocks.html`, `india.html`), scanning many rows.
**Design implication:** sticky table headers, tabular figures, right-aligned numerics, and a
clear row-hover state. The four market sections became a single coherent nav with the current
section marked from the URL rather than hand-maintained per page.

### 2.4 Secondary — the derivatives trader
Uses `india_options.html` for chains, payoff diagrams and spreads.
**Design implication:** the page had **no main landmark at all**; one was added so keyboard
users can skip the HUD. Dense option-chain tables inherit the shared table treatment.

### 2.5 Tertiary — the operator / on-call engineer
Diagnoses issues under time pressure; reads status badges, logs and the mode indicator.
**Design implication:** status badges gained semantic colour with AA-verified contrast and a
non-colour-only cue (text label), so status is not conveyed by colour alone.

### 2.6 Cross-cutting — assistive-technology users
Previously unserved: the codebase had **zero** `aria-*` attributes, **zero** focus indicators
and **zero** live regions.
**Design implication:** this is the largest single quality gain in the change. See §5.2.

---

## 3. Token migration map

`hm_ui.css` re-points legacy names whose values were **identical across all four pages** (a
no-op) or that were **defective**. Names whose values genuinely differ per page are left alone,
because redefining them globally would silently repaint pages that currently look correct.

| Legacy token | Action | New value |
|---|---|---|
| `--text-primary` | re-point (was identical everywhere) | `var(--hm-text-primary)` |
| `--text-secondary` | re-point (was identical everywhere) | `var(--hm-text-secondary)` |
| `--text-dim` | **fix** — was 3.89:1 / 4.19:1, below AA | `var(--hm-text-dim)` = `#8494ab` |
| `--neon-bull`, `--neon-bear` | re-point (was identical everywhere) | `var(--hm-bull)`, `var(--hm-bear)` |
| `--font-mono` | re-point (was identical everywhere) | `var(--hm-font-mono)` |
| `--accent-cyan` | **unify** — was `#38bdf8` vs `#00d4ff` | `var(--hm-accent)` = `#38bdf8` |
| `--font-main` | cross-alias (India-only name) | `var(--hm-font-sans)` |
| `--bg-card`, `--bg-panel`, `--bg-base`, `--bg-dark`, `--bg-main`, `--border-subtle` | **leave alone** — differ per page | migrate per page to `--hm-bg-*` |

Going forward, prefer the `--hm-*` name in all new code.

---

## 4. Design decisions and trade-offs

### 4.1 Small text: contrast over size
The survey found 155 `font-size` declarations, biased to 8–11 px. Globally raising the floor
would overflow the fixed-height rows in a grid that holds 346 load-bearing element ids. For
tabular figures the dominant readability lever is **contrast**, not point size — so the
dim tier was corrected to clear AA (4.88:1 worst-case, up from 3.89:1) and the size ramp was
applied to *new* chrome only. This is a deliberate trade-off, not an oversight.

### 4.2 `!important` is confined to four accessibility guards
`!important` appears 17 times across 10 rule sites, and every one falls into a case where a
later or more specific rule would otherwise defeat a WCAG requirement:

| Where | Why it must win |
|---|---|
| `:focus-visible`, `.hm-skip-link:focus-visible`, `.hm-on-accent:focus-visible` | Eight non-important `outline: none` rules in the page sheets suppress the indicator outright; none restores it. Specificity is deliberately left at `(0,1,0)` so a better-authored rule added later can still win. |
| `.sr-only`, `.hm-sr-only` | Standard visually-hidden clip; must survive any layout rule. |
| `@media (prefers-reduced-motion: reduce)` | The user's OS-level preference must beat any authored animation. |
| `@media (max-width: 600px)` on `input`/`select`/`textarea` | A 16 px minimum font size is the only reliable way to stop iOS Safari auto-zooming on focus. |

No `!important` is used for cosmetic layout or colour.

### 4.3 No `@layer` for the overrides
`@layer` was considered and rejected: un-layered styles always beat layered ones, so wrapping
these overrides in a layer would let the un-layered page CSS win and defeat the purpose. Load
order (last) plus equal specificity is the correct mechanism here.

### 4.4 Alert shim instead of editing 10 call sites
Editing ten `alert()` calls would have touched three page scripts and risked the surrounding
async control flow. Swapping `window.alert` for a shim achieves the same UX outcome with zero
edits to those scripts, and preserves the original message text (including newlines) and the
`undefined` return value. If the toast path ever throws, the shim falls back to the native
dialog so a message can never be silently swallowed.

### 4.5 Two measurements deliberately left alone
Two restyles were **reverted during implementation** after checking the existing values, because
each would have shifted dense layouts for no accessibility gain:

- **Badge metrics.** The page sheets use 9.5–10 px with tight line-height. An earlier draft of
  this layer set 11 px with `line-height: 1.6`, which would have grown every badge by ~8 px
  inside headers and table rows. The shipped rule keeps the compact metrics and changes only the
  *shape* (pill radius) and the colour semantics. Badges need no size uplift because their
  foreground colours already clear AA by a wide margin. Note the base rule itself is a real
  unification: `india.css` and `india_options.css` define **no** `.badge` base rule at all, so
  their badges previously depended entirely on per-variant classes.
- **HUD vertical padding.** The four HUDs disagree: `header.terminal-hud` is a fixed 56 px box
  with **zero** vertical padding, while `.stocks-hud` uses `padding: 10px 20px` and the India
  HUDs `10px 24px`. A single shared `padding-top` for safe-area insets would have collapsed one
  family or squashed the other, so the two are handled separately and use `max()`/`calc()` so
  they can only ever *add* space. Browsers without `env()` support drop the declaration and keep
  the page sheet's padding, degrading to a no-op rather than a regression.

The one intentional convergence: all four HUDs now carry `min-height: var(--hm-hud-height)`
(56 px, 50 px under 600 px), so the three content pages gain a slightly taller header that
matches the terminal. This is a deliberate consistency change.


---

## 5. Verification performed

### 5.1 Functional preservation
Mechanically compared every template against `HEAD`:

| Page | ids | inline `onclick` | classes |
|---|---|---|---|
| `index.html` | 206 → 206, **0 missing** | 71 → 71, **0 missing** | 265 → 266, 0 missing |
| `stocks.html` | 72 → 73, **0 missing** | 39 → 39, **0 missing** | 88 → 89, 0 missing |
| `india.html` | 65 → 65, **0 missing** | 42 → 42, **0 missing** | 128 → 129, 0 missing |
| `india_options.html` | 41 → 41, **0 missing** | 39 → 39, **0 missing** | 88 → 89, 0 missing |

Additions are limited to `hm-skip-link`, `hm-main`, and ARIA attributes. Tag balance validated
for all five templates. CSS and JS both pass syntax validation; `hm_ui.css` references 61 custom
properties and defines all 91 it uses (**0 undefined**).

### 5.2 Accessibility baseline

| Check | Before | After |
|---|---|---|
| `aria-*` attributes (templates) | 0 | tab/dialog/nav/live semantics |
| `:focus-visible` rules (CSS) | 0 | restored, `!important`-guarded |
| Skip links | 0 | 4 (one per page) |
| `<main>` landmark | 1 of 4 pages | 4 of 4 |
| `prefers-reduced-motion` | absent | honoured |
| Live region | absent | `aria-live="polite"` |
| Modal focus trap / Escape / restore | absent | all three production modals |
| Touch targets ≥ 44 px | ~28 px tab bar | 44 px |
| `--text-dim` contrast | 3.89:1 (FAIL) | 4.88:1 (PASS) |

### 5.3 Not verified
**No visual browser regression pass was performed.** Browser automation is unavailable in this
environment. The change is verified structurally, syntactically and by contrast measurement, but
a human should open `docs/ui_preview.html` and then each of the four pages at desktop, tablet and
phone widths before this is considered fully signed off.

---

## 6. Findings recorded during the work

### 6.1 Orphaned UI (dead code — removed)
`app.html` (372 lines), `app_shell.css` (8.7 KB) and `app_shell.js` (4.6 KB) were **not reachable**:
`jarvis/api/server.py` had no `/app` route and no generic template route — it served only
`index.html`, `stocks.html`, `india.html`, `india_options.html`. `app.html` also carried a stale
cache-buster (`?v=20260826_v7`) and referenced globals its page script never assigned.

**Resolved.** All three were deleted in the console redesign (commit `5bfab68`). The redesign
replaced the single-purpose terminal with `console.html`, which is served at `/`, and retained the
legacy terminal at `/classic` as the rollback path. `tools/verify_console_live.py` asserts that
`/static/js/app_shell.js` now returns 404, so the removal cannot silently regress.

### 6.2 Latent nav-convention divergence
`stocks.html` marks the current nav link with `btn-nav-active`; `india.html` and
`india_options.html` use bare `active`, and each page's stylesheet styles only its own
convention. Each page is currently self-consistent, so this is **not a live bug** — but it
would break the moment the sheets were consolidated. Resolved by styling **both** conventions
plus the canonical `aria-current="page"`, and by deriving the active state from the URL at runtime.

### 6.3 Index page nav is present but buried
The terminal *does* link to the other three sections, but only inside a dropdown
(`.dropdown-item.market-nav-item`). Those items are now styled as proper navigation targets with
44 px targets and `aria-current` support. No markup was moved, so the dropdown still behaves
exactly as before.

### 6.4 Stale cache-busters
The four templates referenced `?v=20260907_v1` (and `app.html` `?v=20260826_v7`). All live
references were bumped to `?v=20260912_v2` so the new assets are fetched. Note the server already
sends `no-store` for non-vendor static files with a 5 s in-process cache, so the query string is
belt-and-braces rather than load-bearing.

---

## 7. The redesigned console

The single-purpose terminal was replaced by a three-view console. It is a **new surface**, not a
restyle of the old one, so it does not consume the page sheets listed in §3 — it builds on
`hm_ui.css` tokens directly and defines no new ones.

### 7.1 Routing

| Path | Serves | Notes |
|---|---|---|
| `/`, `/index.html`, `/console`, `/console.html` | `console.html` | The new console. |
| `/classic`, `/classic.html` | `index.html` | The legacy terminal, kept as the rollback path. |
| `/stocks`, `/india`, `/options`, … | unchanged | Untouched by this work. |

The retired `app.html` / `app_shell.css` / `app_shell.js` are gone (§6.1).

### 7.2 Three views

* **Trade** — watchlist plus AI auto-selection, chart plus open positions, and an order ticket with
  a "Why this trade" panel that shows the reasoning behind the selected setup.
* **Backtest** — a requirements form (objective, symbols, modes, constraints, search grid), live
  progress, results, and job history.
* **Analytics** — account metrics, per-mode reliability, and trade history.

### 7.3 Responsive contract

The layout is driven by `body[data-view]` and `body[data-panel]`, so what is displayed and what the
controls claim can never disagree.

| Width | Layout |
|---|---|
| ≥ 1024 px | Three columns. |
| 768–1023 px | Two columns. |
| < 768 px | One column, switched by the panel bar. |

Breakpoints are 1023 px / 767 px / 420 px, plus reduced-motion and print rules.

### 7.4 New features

* **Automatic trade selection across modes.** `jarvis/intelligence/mode_aggregator.py` adds the
  cross-style consensus layer that the arbiter never had: candidates are grouped by symbol, each
  style's vote is weighted by what it has actually earned on the 6-month history, and agreement is
  measured by summed utility rather than headcount. One style voting alone is never tradeable by
  construction — see the module docstring for the full argument.
* **Requirement-driven backtesting on real MT5 data.** `jarvis/backtesting/optimizer.py` searches
  entry-selectivity and exit geometry per symbol against the real cached history, with explicit
  costs, hard constraints, and a held-out validation window.
* **API surface.** `jarvis/api/intelligence_api.py` exposes both, behind authentication. The HTTP
  surface has no path that can open a trade: auto-selection is hard-wired to a dry run.

### 7.5 How to verify

Run `python tools/verify_console_live.py`. It boots the real server on a scratch port and asserts
every route above, including that the retired assets 404, that auto-selection stays a dry run when
asked otherwise, and that it reports `UNAVAILABLE` rather than an empty success when no engine is
attached. Unit coverage lives in `tests/test_mode_aggregator.py` and
`tests/test_backtest_optimizer.py`.

## 8. How to review

1. **Open `docs/ui_preview.html`** — a live gallery of every token and component, including the
   measured contrast table and working toast/dialog demos. This is the fastest way to review.
2. **Run the app and visit each page** at desktop, tablet (≤900 px) and phone (≤600 px) widths.
   Confirm the shell looks right and that existing interactions still work.
3. **Keyboard pass:** `Tab` from page load — the skip link appears first; the focus ring is
   visible on every control; `Escape` closes any open modal; arrow keys move the mobile tab bar.
4. **Reduced motion:** enable it at OS level, reload, confirm the pulse dot and toast
   animations stop.

## 9. Rollback

Fully reversible and low-risk: remove the two `<link>`/`<script>` references to `hm_ui.css` /
`hm_ui.js` from the four templates. No page stylesheet or page script was modified, so the UIs
revert to their prior appearance and behaviour exactly. The two new files can be deleted.
