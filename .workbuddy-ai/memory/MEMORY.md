# HM-AI / HM Algo 2.0 — index

Injected every session and **silently truncated at the tail — keep it small.** Everything here is a
pointer or a rule that has already cost a session; detail is elsewhere on purpose.

* **`TRAPS.md`** — frontend, testing, data-source and data-integrity traps + project conventions.
* `AUDIT-2026-09.md` — signal-quality evidence, P0/P1/P2 backlog. `YYYY-MM-DD.md` — per-session detail.
* Skills: `diagnose-git-push-auth`, `recover-vanished-working-tree`.
  `audit-trading-system-integrity` §5 now holds the **measurement** traps (stale scan manifest
  misaligning `bar_idx`, `cpu_percent` lying, 1/20-at-0.95 = false-positive rate, grid-boundary
  artefact, effective-n inflation, expiring conventions) — §§1-4 are wiring only. Check it before
  auditing signal quality.

## Non-negotiables

* **`.git/` is not safe here.** Files vanish in the small hours and cannot be recreated (4 incidents;
  the last two followed `git rm` / `git stash push`; cause unknown). Commit and push early, **never
  `git stash`**, keep `git bundle create .git/backup/repo-<ts>.bundle --all` fresh. Some paths are
  readable but **not writable** (`HM_dashboard.bat`, `HM_start.py`,
  `jarvis/intelligence/decision_engine.py`) — write via a hardlink alias in `.scratch/_restore/`;
  `open(alias,'w')` **truncates the shared object**.
* **The live server does not hot-reload.** Python edits do nothing until restart; static
  CSS/JS/templates *are* re-read per request. *A hang that does not reproduce in a fresh interpreter
  is a stale process, not a logic bug.* `py-spy dump --pid <pid>` attaches without restarting;
  `netstat -ano | grep 8501` for the pid.
* **Push** hangs a long time (observed 40s, but also **3m09s** — do not assume a failure at the
  40s mark) and `git status` always says `[gone]` (`.git/refs/remotes/*` is wiped
  immediately after being written). Bypass both: run the push in the **background** redirecting to a
  file, then verify with `git ls-remote` — never the push message and never the exit code alone
  (a hung-but-successful push has shown `EXIT=0` only after minutes). Backticks in `-m` are eaten by
  bash: use `git commit -F <file>`. `/tmp` does not exist; use `.scratch/`.

## Running the platform

`HM_start.py [paper|live]` boots the engine + MT5 client + web server — **real MT5 market data;
`paper` = simulated fills**. `HM_dashboard.bat` is **UI + REST API only** (`mt5_client=None`, so
`auto-selection` answers **503** and `MT5` reads `DISCONNECTED` — both expected, not a fault). Check
`psutil.Process(pid).cmdline()` before concluding the data path is broken. **Never finish a task with
the dashboard alive only as a session background task** — point the user at `HM_dashboard.bat` and say
plainly that closing the window stops it.

## Baselines

**pytest 1356 passed / 20 deselected** (883 → 887 with `test_history_window_filter.py`, → 914 with
`test_copilot_intents.py`, → 922 with `test_action_response_contract.py`, → 924 when its drift guard was
rebuilt, → 970 with `test_copilot_memory.py` (22) and `test_copilot_provider.py` (23) plus one restored
branch test, → 998 with `test_measurement_spine.py` (28), → 1054 with `test_remote_auth.py` (56),
→ 1246 with `test_market_sessions.py` (192), → 1324 with `test_trade_guard.py` (78), → **1356** with
`test_hrp_allocator.py` (32); 657 when this skill was written). `tools/`:
`verify_ui_live.py` (46 — was 44; two `/api/copilot/ask` probes. **Starts its own server on :8599** —
nothing may be listening there, or its probes hit the engine-less scratch server and read as
`attached=False` / `503`, which looks exactly like a regression. Run the browser suites against
**`.scratch/srv8611.py` on :8611** instead. Its total is *conditional*: 45 with no engine, 46 with one) ·
`verify_dashboard_render.js` (**209** — was 88; the backtest fixture added 23, the history fixture 19,
auto-selection + regime-policy 22, the order path 15, the copilot panel 15, trade markers 8, the
Analyst/News context strip 18. Its chart stub records `setMarkers`; a no-op stub there made
`drawTradeMarkers` wholly unobservable. **A stub must mirror the real markup's initial state**: the strip's
panels ship `hidden` + `data-state="loading"` in `dashboard.html`, and both renderers early-return on a
hidden host — leave them visible and the full analyst panel's refresh makes `setContext()`'s own render
look redundant. Build nodes with `elementFor(id)`, never `registry.get(id)` (a lazy Map: un-queried ids
return `undefined`)) ·
`verify_copilot_render.js` (**23** — the copilot answer renderer exists in **two** front ends and this
evaluates both and asserts they agree; it exists because they had already drifted into a formatting bug
in one and an XSS in the other) ·
`verify_terminal_render.js` (**55** + 5 FIND/CLEAR measurements — the classic terminal, which had **zero**
renderer coverage before it; `terminal.js` registers `fetchHistory` *only* inside `setInterval`, so an
inert `setInterval` stub makes the history table — and every assertion on it — a silent no-op. Capture
the intervals and tick them) ·
**`tools/dom_stub.js`** holds the stubbed DOM both render harnesses share
(`createDom({templateIds, docRoots, templateTree})`); a copy per harness would drift invisibly. It is
**not** under `lib/`: a bare `lib/` in `.gitignore` matches at any depth, so `tools/lib/dom_stub.js`
worked locally and was absent from every clone — check a new file with
`git check-ignore -v <path>`. Assert
against markup **as rendered** — never
`html.replace(/\s+/g,'')`, which eats the space in `<span class="…">` and makes a correct string fail;
bind a value to its own label via `metricValue(html, label)` or a swapped counter passes) · `verify_dashboard_nav.js` (31) · `verify_ui_layout.js`
(**238** as of the ticket work — the total is *not* fixed: it counts controls per viewport, so hiding a
control lowers it, and 238 → 229 was the ticket's pending row correctly disappearing, not a lost check.
Read the FAIL lines, never the total) · `audit_endpoints.py` (**46** — rises when a new route is added; add POST-only
routes to its `POST_ONLY` set or they report DEAD, and **probe with `--base` against a server built
from the current tree** or a stale engine makes new routes look dead) · `audit_wiring.py` (135
modules, 0 broken refs).
Screenshots: `.scratch/shot_one.js <tag> <page>` — honours `JARVIS_PORT` to point at the standalone
`:8599` instance instead of the user's engine (**`agent-browser` does not support Windows**).
Run the browser suites **one at a time**: three in parallel plus a probe starved `forex`/`options`
into 45s navigation timeouts, which read exactly like a regression.

## Rules worth repeating

* **Execution mode must not gate market data.** Paper mode skips `mt5.initialize()`, so the data path
  must call `broker_symbols.ensure_mt5_terminal()` itself or every frame silently becomes synthetic.
  Health flags must be **measured**, not inferred from the execution login.
* **The UI has no request timeout anywhere** — an empty result must still repaint, and a first-paint
  watchdog must state a stall.
* **MT5 times are BROKER-SERVER time, not UTC.** Never hardcode the offset; use
  `jarvis/data/broker_time.py`. Local probes: **`curl --noproxy '*'`** (a proxy is configured and
  otherwise answers "upstream connect failed" for a healthy server).
* **A frontend that reads a key the server never sends renders the empty state on success** — three
  instances now (backtest `per_symbol`, `/api/history` read as an object, `grid_dimensions` never
  whitelisted). Read the real payload before writing its reader.
* **A refused order is answered with HTTP 200**, so `res.ok` says nothing about whether it happened:
  `mt5_client` reports `{"status": "FAILED"|"BLOCKED", "reason": …}` in the **body** and the route
  passes it through. Decide an action's outcome from that status, never from the code — `manual_trade`
  branched on `res.ok` alone and told the trader a **rejected market order was "submitted"**. The broker
  sends `reason`, the server's own validation sends `error` with HTTP 400. `tests/test_action_response_contract.py`
  keeps the UI's refusal set and the backend's emitted set in step.
* `executed_trades.timestamp` is **ambiguous** (entry for an engine-logged trade, exit for an
  MT5-synced one). Use `closed_at`, which is null unless the row really is closed. **Both front ends now
  do** (the dashboard first; the terminal's column was headed "Execution Time" over `timestamp`, so the
  header was false for half the rows — fixed in round 10, header now "Closed"). Two front ends, one fix:
  when a defect is shared, grep the other front end before calling it done (`verify_terminal_render.js`
  guards the pair, and its cross-file check flipped from "dashboard only" to "both").
* **A fetcher registered inside `setInterval` is invisible to a stubbed clock.** `terminal.js` fetches
  telemetry/radar/news/candles at boot but registers history only as `setInterval(fetchHistory, 5000)`,
  so a no-op `setInterval` leaves the history table empty and every assertion about it passes vacuously.
  Capture the intervals and tick them.
* **Escaping an inline handler needs the JS layer escaped FIRST — HTML entities are decoded before the JS
  runs.** `escapeHtml()` turns `'` into `&#39;`, which looks like it protects
  `onclick="window.setSymbol('<symbol>')"`. It does not: the parser decodes `&#39;` back to `'` before the
  JS is parsed, so the string still closes. A value inside an inline handler is a JS string inside an HTML
  attribute and needs both layers, JS first —
  `escapeHtml(s.replace(/\\/g,"\\\\").replace(/'/g,"\\'"))` (`escAttr()` in `terminal.js`). Applied to all
  three inline-handler sites plus every server string reaching `innerHTML`: **3 → 41 call sites**. Verify
  with a **sweep**, not by eye: list every `${…}` inside an `.innerHTML` assignment, exclude those already
  wrapped in `escapeHtml(`/`escAttr(`/`Number(`/`formatPrice(`, expect 0 (an unfiltered sweep reported 77
  and was useless — most hits were `textContent`, `alert()` and `fetch()` strings, none of which parse
  HTML). When measuring the fix, read the **raw `innerHTML`**, not `deepHtml()` — the latter re-adds
  decoded `_text` and cannot tell a parsed tag from text that looks like one — and count **unescaped**
  quotes, because the payload text still appears after escaping, preceded by a backslash.
* **A guard that filters its inputs through the set it validates is a tautology.** `test_action_response_
  contract.py` "checked" that every refusal the broker can emit is one the UI knows, by reading the status
  literals and then intersecting with `{"FAILED","BLOCKED","REJECTED","ERROR"}` — so the set under test was
  a subset of that literal *by construction* and a new status was filtered out **before** the assertion
  could fail. The comment promised "a new one shows up"; the code made that impossible. Prove a guard with
  a **mutation**: append a plausible new status (`{"status": "PARTIALLY_REJECTED"}`) and confirm the test
  goes red — restore in a `finally` and assert byte-identity. The replacement classifies exhaustively
  (`REFUSALS` / `COMPLETIONS` / `NON_2XX_ONLY`) so an unknown status *forces* a decision. Scope the
  enumeration to the route's own dispatch (`do_POST`'s `/api/action/` block), not the whole file: the
  status endpoint emits `SAFE_MODE`/`OPERATIONAL` about the system, a different vocabulary.

* **The copilot: memory carries a referent, the model is additive only.** `JarvisCopilot.ask(q, context,
  session_id)` — with no `session_id` it is stateless and byte-identical to before (the one-line curl and
  the 27 older tests depend on it). `ConversationMemory` records only `(intent, symbol)`, **never** an
  answer: every figure is re-read from `state_manager` at answer time, and the test that proves it mutates
  the state *between* two turns. The optional LLM (`jarvis/api/copilot_provider.py`, OpenAI-compatible,
  `JARVIS_COPILOT_API_KEY`) is consulted **only** when the router returned the help text, so a grounded
  number can never be replaced by prose; the key lives in the `Authorization` header and nowhere else —
  not the body, not a log line, not an error string — and any failure returns `None` so the rule-based
  answer stands. Provider-agnostic, so no vendor choice is forced; inert with no key.
* **A test stub missing a field the code reads is a coverage hole, not a simplification.** `_decision()`
  omitted `symbol`/`regime`/`bull_case`/`bear_case`/`quality_gate.passed`, all of which
  `ReasoningEngine.generate_explanation` reads — so the copilot's `analyze <symbol>` branch had **never
  executed in any test** and raised the first time one did. When a branch seems untested, check whether the
  stub *can* reach it before concluding the branch is fine.
* **A mutation that passes proves nothing.** My first attempt injected a branch reading
  `carried['_cached']`, a key nothing ever sets, so the mutated code never ran and the suite stayed green.
  Always confirm the mutation actually **fails** the suite — if it doesn't, the mutation is dead, not the
  guard.
* **An assertion that cannot fail is worse than none.** "markers are sorted by time" passed with the
  `.sort()` deleted, because every event in that fixture fell after the loaded window and all snapped to
  the same bar — the order carried no information. Mutation-test the assertion; if it survives, delete it
  and say why in a comment. Corollary: **a mutation that passes may never have executed** — mutating
  `utcSeconds(t.closed_at)` to fall back to `timestamp` still passed because an earlier `closed_at` guard
  returned first; only removing both exposed it.
* **Python's `read_text()`/`write_text()` round-trip converts CRLF → LF silently.** After restoring a file
  from a mutation, `git status` said modified while `git diff` was *empty* (`git ls-files --eol` →
  `i/lf w/crlf`). `git checkout -- <file>` clears it. Never infer "changed" from `git status` alone here.
* **`curl -s -o /dev/null -w '%{http_code}'` exits 23**, so an `&&` chain built on it silently skips every
  later step while still printing the code — it looks like the probe worked and the following commands just
  produced nothing. Use `;` to separate probes. And **`--noproxy '*'`** is mandatory here.

## Signal quality

**The entry signal has no measured edge — now measured four independent ways.** It *loses* money on
real MT5 data (H1 365d, 94,937 trades: mean R −0.0509, cluster-robust t −3.04, p 0.0067, CI excludes
0); 8/20 per-symbol verdicts flip when the window is halved; only 3/20 beat always-long in both
windows against 5 expected by chance; the refitted score calibration is 0/20 skillful in both
windows (AUC→0.5 as n doubles); and **DSR > 0.95 is met by 0/20 symbols in either window** once
overlapping trades are counted honestly — 94,937 rows are worth **327 independent bets (0.3%)**.
Gate 100% hand-authored; meta-label gate inert (AUC 0.481). Full evidence + the P0/P1/P2 backlog:
**`AUDIT-2026-09.md`**.

**Cost basis is a measurement, not a detail.** Every candidate table had to be regenerated after the
SELL-leg fix (`fdf8e58`); before that, half of ~95k audited trades carried no spread at all. Two
independent errors in *my own* measurement tools each moved the verdict — a biased always-long
control (1/20 → 3/20 survivors) and a 10× units error. **Re-derive nothing the scanner already
stored: consume `spread_pips`, and never multiply the bars' raw `spread` by `pip_size` (MT5 reports
points).**

## Environment

Sandbox refuses writes outside the project dir. Bash, not PowerShell. `taskkill` needs
`MSYS_NO_PATHCONV=1`. Python 3.13.12 managed at
`…\binaries\python\versions\3.13.12\python.exe` — works without a venv, though
`…\binaries\python\envs\default\Scripts\python.exe` also exists and two skills cite it.
`rm -rf X && cmd` swallows the command's stdout — run the `rm` separately. Running a script *by path*
puts the **script's** dir on `sys.path`. The server binds **127.0.0.1 only**, so
`http://<LAN-IP>:8501` never works.
