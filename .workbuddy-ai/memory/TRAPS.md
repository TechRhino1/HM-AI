# Frontend & testing traps — each one has cost a session

Companion to `MEMORY.md`, which is injected every session and so must stay small. Read this file on
demand: when touching the UI, when writing a test that has to discriminate, or when a page misbehaves
in a way that no error message explains.

## Frontend

* **A self-retriggering MutationObserver freezes the page with no error.** `setAttribute` queues a
  record even when the value is unchanged, so an observer writing inside its own `subtree` +
  `attributeFilter` re-queues forever; microtasks drain before paint, so the thread blocks permanently
  and silently. Rule: **a callback must write nothing it observes** (`setIfChanged` + re-entrancy flag).
  It only fires on *interaction*, which is why an idle page looks perfectly healthy.
* **A definite size that was never stated resolves from content.** Two instances, same family:
  * an implicit `auto` grid column is sized by item min-content — a 404px tab strip made
    `documentElement.scrollWidth` **421px on a 390px viewport**, on every page;
  * `.tt-app { min-height: 100vh }` leaves the grid container indefinite, so the `1fr` main row
    resolved against *content* and a view grew to **1263px on a 900px screen**.
  Fixes: `grid-template-columns: minmax(0, 1fr)` + `min-width: 0`; and a definite shell height
  (`100vh`/`100dvh`) + `overflow: hidden` on main, with the **view** owning the scroll. Trade opts out
  because its panes scroll internally. **A scroll container still needs `min-width: 0`** or it
  contributes its content width to the parent.
* **`overflow: hidden` deletes a flex row's surplus with no scrollbar.** A panel head needing 626px in
  a 372px panel silently dropped its last controls — present in the DOM, never drawn. Fix:
  `height: auto; min-height: <head token>; flex-wrap: wrap` — "wrap only if it does not fit", so a row
  with room is pixel-identical and there is no breakpoint to get wrong.
* **Liquid glass**: tokens + `.hm-glass` live in `hm_ui.css` (loaded by all six pages);
  `theme_terminal.css` (dashboard only) adds an ambient radial background — a blur over a flat colour
  is invisible. Two traps: the sheen/tint must be `background-image` **layers**, not an absolutely
  positioned `::before` (a positioned pseudo-element paints *above* non-positioned in-flow text); and
  an `@supports not (backdrop-filter…)` fallback must be declared **after** the rules it overrides, or
  it loses on source order and ships a translucent, unblurred panel.
* **`apiRequest` never rejects.** Its `.catch` normalises every transport failure — including
  `Failed to fetch` and aborts — into a **resolved** `{ok:false, status:0, error}`. So a missing
  `.catch` on a caller is *not* a bug and adding one is dead code. The real defect was that `error`
  was the browser's literal **"Failed to fetch"** rendered to a trader. Fixed centrally in
  `dashboard.js:apiRequest` and `console.js:getJSON/postJSON` (the latter corrects ten catch sites
  without editing any of them). **The console "Failed to fetch" line is Chrome's own network log, not
  an unhandled rejection** — do not "fix" it by adding catches.
* **A `position: fixed` descendant of `.tt-rail` cannot blur page content**: the rail's `z-index`
  creates a stacking context, and `backdrop-filter` on an ancestor also creates a containing block for
  fixed children. That is why the mobile nav is a top app bar, not a bottom tab bar.
* `.tt-rail` is a sticky **top bar** (`grid-area: rail`). `[hidden]` is a weak UA rule — declare
  `.panel[hidden] { display: none; }`. Templates are **static HTML** (0 Jinja placeholders), so layout
  edits need only a browser refresh, no restart.
* **Named grid areas beat source order.** `.tt-slot--<name>` + `grid-template-areas` states placement
  once. Relying on DOM order and patching it with breakpoint overrides is how two `max-width` blocks
  came to disagree about how many columns a view had.
* Canonical nav: `/` Forex·Crypto, `/stocks` US, `/india` India, `/options` India Options; held in a
  `.tt-dropdown` in `dashboard.html`, styled by `markActiveNav()` in `hm_ui.js`.
* `/api/telemetry_state`'s `account` already carries `login`, `name`, `server`, `company`, `balance`,
  `equity`, `margin`, `free_margin`, `margin_level`, `leverage`, `profit`, `currency`, `trade_allowed`
  and `last_sync_time` — the account dropdown needs no server change.
* Diagnosing a blocked main thread: `page.evaluate` ignores its own `timeout`, so race it against a
  timer and run a control phase. `Debugger.enable` + `Debugger.pause` names the blocking frame (no
  pause ⇒ the block is native).

## Testing

* **Prove the test fails on the pre-fix code** — temporarily restore the bug, re-run, revert. An
  assertion that never saw the bug pins nothing. (The broker-clock fix was verified this way: 3 of 13
  tests fail pre-fix, including `'DAY_TRADING' != 'SWING'`.)
* Hunting an exception misses unbounded recursion when a frame swallows it — **pin the call** instead.
* `hash()` salting is constant *within* a process, so an in-process test passes against the bug — the
  discriminating test must spawn a subprocess under a different `PYTHONHASHSEED`.
* Clear both caches in `setUp`; `_quote_cache` has a 15s TTL.
* Plain `pytest -q` exits 1 *after all tests pass* (a safe-delete hook blocks temp-dir cleanup) — use
  `python -m pytest -q --basetemp=.scratch/pttmp`.
* `np.allclose` on microsecond epoch ints has an rtol far larger than a 4-hour shift — compare indexes
  with `.equals()`.
* A "dynamic" knob whose argument is never passed is dead code — **grep the callers** before trusting
  it. This is how `atr_ratio` was found.
* **A silent clamp is a bug hider.** `max(0.0, negative)` did not merely produce a wrong number, it
  turned a loud sign error into a plausible zero that also satisfied the caller's `> 0` guard. If you
  clamp, warn.
* **A check that can pass on broken input is not a check.** `audit_endpoints.py` tolerated a timeout as
  "needs a live provider", which is how three broken India routes stayed invisible for weeks.
