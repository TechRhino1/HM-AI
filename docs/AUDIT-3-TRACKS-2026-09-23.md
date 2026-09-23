# HM-AI / JARVIS — Three-Track Audit Report
*Wednesday 2026-09-23. Read-only investigation. No code modified.*

---

## Headline

The recurring losses are dominated by a **mechanical stop-doubling defect** (median 0.21×ATR; 92% of trades have a stop < 0.85×ATR). On 137 real broker closed trades: **−$633.33, win rate 25.5%**. Fixing the stop floor is mechanical and *reduces bleeding* — but the prior signal-quality audit already established `DSR > 0.95` met by **0/20** on **327 independent bets**, and the most recent `entry_edge_verdict.md` (18,998 candidates) states *"There is no edge in the current entry features… `score` sorts how badly trades lose, not how well they win."* **No entry-model change can produce profitability until that verdict is overturned with evidence.** The FVG/ICT entry model the user asked about is *already partially implemented* in `institutional_entry_engine._find_micro_fvg_ce` (drives SCALP/DAY at the gap's 50% CE) — and the system still loses. Per the user's own instruction ("evaluate before implementing"), the FVG swap is **WAIT**: implement only after a standalone FVG backtest passes DSR > 0.95 on *effective* n AND beats always-long, both instruments already in-repo.

---

## Track 1 — Loss analysis & entry-model evaluation

### A. Root causes (measured on real closed trades)

| Symbol | n | WR | med stop / ATR | med R:R | P&L |
|---|---|---|---|---|---|
| XAUUSD | 45 | 38% | **0.06×ATR** | 13.8 | **−$414.14** |
| GBPUSD | 25 | 24% | — | 5.0 | −$86.46 |
| BTCUSD | 24 | 25% | **0.09×ATR** | 2.6 | −$14.04 |
| OILCASH# | 24 | 17% | — | 0.0 (tp=0 sentinel) | −$2.37 |
| EURUSD | 9 | 11% | — | 2.2 | −$7.70 |
| SOLUSD | 6 | 0% | — | 18.6 | −$103.17 |

- **Sizing (P0 cause).** `dynamic_levels.py:273` floors `min_sl_dist` at `max(3×spread, 0.10×ATR)`. `institutional_entry_engine.py:131,341` floors only at `pip_size*5`. Result: stops sit inside the noise band; TP then becomes an unreachable multiple of a near-zero risk, minting the absurd R:R values above. The same class as the **0.7-pip stop → 5-pip re-anchor** defect (`955d40c`); still visible in 2026-09-22 fills.
- **Entry quality.** Hand-authored 7-branch `if/elif` on `choch`/`bos`/`trend_score` (`decision_engine.py:137-154`), hand-typed 6-bin confidence table (`confidence.py:13-20`) blended with unfitted ML weights. Verdicts are negative (`entry_edge_verdict.md`).
- **Execution.** Correcting for real spreads drains **0.162R/trade** (median 0.118R); SOLUSD spends 90.1% of a 1R target on round-trip cost; NZDUSD 81.8%; USDCAD 61.5%; AUDUSD 51.5%; USDCHF 50.6% (`AUDIT-2026-09.md` P0-3 D). WTI refused at `position_sizing.py:146-152` ("Min lot would force 3.29% risk") while EURUSD/AUDUSD — two of the worst symbols — were taken.
- **State bugs.** `state.activeMobileView` is **UI-only**; it does not feed entries. The genuine entry-risk state bugs are: (a) the bar-rescale hack at `institutional_entry_engine.py:477-488` which silently rescales bars by up to 10%+ when `last_close ≠ current_price`, feeding a *rescaled (unobserved)* series into the entry; (b) entry prices minted from `context.ask/bid=0.0` are now refused (`dynamic_levels.py:99-127`).
- **Not measured.** MFE/MAE are 0.0 on every row (D19); no `exit_price`; no fee/slippage columns. Adverse excursion at entry cannot be derived from the data.

### B. Entries near support/resistance — VERIFIED

The entry does **not** anchor to horizontal S/R. `dynamic_levels.py:187,325` enters at market `ask`/`bid`. S/R objects (`demand_zone`, `order_blocks`, `fair_value_gaps`, `key_levels`) are consumed **only** to build the stop (`dynamic_levels.py:190-226`) and target (`:279-302`). The model is a displacement/retracement model (FVG midpoint, OTE 61.8–70.5%, order block), not a horizontal-S/R model.

Historical check, last 56 joinable real closed trades vs the H1 swing set (pivot w=5):

- Within 5 pip of a swing: **29/56 (52%)**; within 10 pip: 33/56 (59%); within 20 pip: 41/56 (73%).

Control: every bar's close vs the same swing set gives EURUSD/GBPUSD **99%** within 5 pip (median 0.2 pip) and XAUUSD **44%**. **Entries are no more clustered at swings than random bars — proximity is incidental, not intentional.**

### C. FVG evaluation — WAIT (do not implement as a replacement)

- **FVG is already partly shipped.** `jarvis/market/fair_value_gap.py` (3-candle gap, 50-bar lookback, mitigation tracking), `jarvis/market/market_structure.py:111-154` (FVG + OBs with 45% displacement), and `institutional_entry_engine._find_micro_fvg_ce` (`:568-599`) which drives SCALP/DAY at the gap's 50% CE. The system still loses.
- **Data exists for an honest backtest.** `data/market/real/<SYM>/<SYM>_{M1,M5,M15,H1,H4,D1}_*.parquet` for 20 symbols (256 files). M5/M15 183d full; H1 365d full to 2026-09-15; M1 183d only from 2026-07-06 (~67d).
- **The honest backtest recipe:** 20 symbols; M15 183d primary + M5 183d execution + H1 365d structure; trigger = 3-candle gap with body/range ≥ 0.55 (already the code's threshold at `:563`); entry at the gap CE on retrace (not the edge — avoids lookahead); stop beyond the gap + buffer; target 1.5R; charge the bars' own `spread` column via `jarvis/backtesting/fills.py`. Reuse `signal_scan.py` + `trade_simulator.py`. Judged by **DSR > 0.95 on effective n** AND beating always-long (both tools exist: `tools/deflated_sharpe_report.py`, `tools/p0_1_direction_audit.py`).
- **Fundamental weigh-in.** `deflated_sharpe_365d.json` → `n_pass_dsr_selected_effective_n = 0`, `n_pass_dsr_production = 0`, portfolio `dsr = 0.0`, 94,937 rows → 327 independent bets (0.3%). Only 5/20 pass on the raw row count, and all collapse under effective n. **The incumbent has no measured edge; replacing it with FVG is only defensible if FVG is measured to have one.**
- **Recommendation: WAIT.** Implement only after the backtest clears both bars. Otherwise the swap is unproven → unproven.

### D. Ranked next actions (review only — nothing changed)

| # | Action | File | Smallest change | Why ranked here |
|---|---|---|---|---|
| 1 | Stop floor ≥ 1.11×ATR everywhere | `dynamic_levels.py:273`; `institutional_entry_engine.py:131,341` | Raise `min_sl_dist` to `max(3×spread, 1.11×ATR)` and apply in both paths | Mechanical; 92% of trades sit inside the noise band. Expect fewer instant stop-outs; expect *no* profitability on its own. |
| 2 | Stop minting R:R from collapsed risk | `institutional_entry_engine.py:369-380` | Recompute TP off the *floored* `risk_dist`; reject rows whose recorded R:R exceeds a sane cap | Artifact of action #1; R:R 13.8/18.6 is fake. |
| 3 | Persist `exit_price` + real `realized_pnl` | `jarvis/data/database.py` INSERT/UPDATE | Add columns; compute P&L from fills (AUDIT-TRADES C3/M7) | **109/109** closed rows have `realized_pnl == expected_value` — entry quality cannot be measured until this lands. |
| 4 | Remove the bar-rescale hack | `institutional_entry_engine.py:477-488` | Return the frame unchanged, or `None` when `\|last_close − current_price\|/price > 0.10` | Feeds rescaled (unobserved) bars into the entry. |
| 5 | Turn on the calibrated entry policy | `config/settings.json` → `trading.use_calibrated_entry_policy` | Flip the flag (AI9 wire exists) | Refuses 11/16 symbols (incl. XAUUSD −0.056 OOS); narrows the book to 5. **Trading decision**, not a bug fix. |
| 6 | Verify the C1 risk clamp on live rows | `jarvis/risk/position_sizing.py:110-117` | Read-only `tools/audit_trades.py` re-run | 27 historical breaches; verify fix on the 137 real rows. |
| 7 | Dedup-aware reporting | `tools/audit_trade_quality.py` | `one_position_at_a_time=True` by default | Counts inflated ~25× without it. |

**Not measured (not findings):** adverse excursion at entry, partial-fill/slippage on the 137 real rows, per-symbol edge claim.

---

## Track 2 — Refactor

### Inventory

| | |
|---|---|
| `jarvis/` Python | 139 files · 42,726 LOC (14 packages) |
| `tests/` Python | 112 files · 29,682 LOC |
| Repo-root `.py` | 35 files · 4,392 LOC |
| `tools/` `.py` | 43 |
| Banned modules in `jarvis/` | **0** (no `subprocess`/`os.system`/`eval`/`exec`/`pickle`) |
| Unused imports | 9 names in 5 files |
| Orphan subdirs | 0 |

### Proposed deletion list (all evidence: 0 refs in tests/docs/imports)

| Path | Reason |
|---|---|
| `debug_eurusd.py`, `debug_eurusd2.py`, `debug_eurusd3.py` | Stale scratch, imports `jarvis` |
| `check_spreads.py`, `check_all_syntax.py` | Scratch utilities, no docs/tests |
| `verify_mt5_data.py`, `verify_exact_before_after.py`, `verify_system.py` | One-off probes |
| `reset_active_positions.py` | Dangerous one-off (mutates live state) |
| `get_remote_mobile_access.py` | **Prints hardcoded `hm2026admin` password** |
| `test_all_dashboard_endpoints.py` | Root smoke script, not collected |
| `test_auth_api.py`, `test_login_api.py`, `test_close_positions_api.py`, `test_india_markets_api.py`, `test_stocks_screener_api.py` | Same — manual HTTP probes, not in `tests/` |
| Root DBs `jarvis_history.db`, `jarvis_trade_memory.db`, `jarvis_circuit_state.db`, `jarvis_drawdown_state.db` | Abandoned duplicates (data/ has the live ones; root user_version=0) → **archive, don't delete** |
| `config/winrate_profiles.{MARGIN0,MARGIN17,PRE_SLIPPAGE,UNGUARDED,WIDEGRID,BAK,PRE_FIX}.json` | 0 code refs (~600 KB tracked) → archive |

### Proposed consolidation

- **`jarvis.bat`≡`JARVIS.cmd`** (byte-identical, 229 B; both hardcode `C:\Users\musu9\...`) → one wrapper.
- **`HM_start.bat`≈`HM_start.ps1`** → one wrapper.
- **`HM_start.py` (371 L) + `jarvis.py` (97 L) + `main.py` (89 L)** all build `JarvisOrchestrator`. Keep `main.py` canonical; `HM_start.py` as the live+tunnel launcher.
- **`jarvis/application/timeout_guard.py`** → move to `jarvis/common/`. Imported by 4 lower layers (`market/data_feed.py:17`, `analysts/parallel_runner.py:23`, `data/market_data_provider.py:411`, `execution/mt5_client.py:13`) — the only cause of `market→application` and `analysts→application` upward edges.
- **9 unused imports** (e.g. `decision_engine.py:8,40`, `risk_engine.py:19`, `orchestrator.py:12,14,37`, `exit_policy.py:43`, `market_context.py:6`).

### Dependency graph (current → target)

**Current** (cycles marked ⟲): `api→everything`; `application→analysts,config,data,execution,intelligence,learning,risk`; `intelligence⟲backtesting` (winrate_targeting imports trade_simulator); `market⟲intelligence`; `market→application` (timeout_guard); `data→india,stocks`.

**Target** (strictly downward): `config` (leaf) → `data,market` → `risk,learning,analysts` → `intelligence,execution` → `backtesting,application` → `api`; `india,stocks,historical` → `data,market` only.

Achieve by: relocate `timeout_guard`; invert `data→india/stocks` behind a registry; move `order_flow` out of `market_context` into `intelligence`; inject a simulator into `winrate_targeting` rather than import `backtesting`.

### Phased execution (each regression-free)

| Phase | Content | Risk | Test changes? |
|---|---|---|---|
| **1** | Pure deletions (10 scratch + 6 root `test_*`) + archive 4 root DBs + 7 winrate-profiles | Zero — no references | None |
| **2** | Wrapper consolidation (`jarvis.bat`/`JARVIS.cmd`/`HM_start.bat`/`HM_start.ps1`); demote `jarvis.py` | Low — entry-point edits only | None |
| **3** | Move `timeout_guard.py` to `jarvis/common/`; update 4 import sites; remove 9 unused imports | Low — mechanical | None |
| 4 (deferred) | Break `market⟲intelligence` and `intelligence⟲backtesting` cycles via shims | Med — touches decision paths | Yes |

---

## Track 3 — Architecture

### Top 10 gaps (scored)

| # | Finding | File:line | Sev | Fix LOC |
|---|---|---|---|---|
| 1 | **No liveness/readiness endpoint.** `/api/diagnostics` is public, unauthenticated GET, returns full account snapshot (`login`, `server`, `balance`, `equity`, `name`, positions, services). | `server.py:367, 729-736`; `state_manager.py:224-237` | **High** | ~20 |
| 2 | **Health signals exist but are unreachable.** `MT5Client.broker_lock_health()` and `TimeoutGuard.health()` are implemented and never called. | `broker_lock.py:115`; `mt5_client.py:1079`; `timeout_guard.py:64` | **High** | ~15 |
| 3 | **Loopback auth bypass is total.** Any request from 127.0.0.1/::1 without `X-Forwarded-For` is auto-`ADMIN`. A tunnel is live (`active_tunnel_url.txt` → `trycloudflare.com`). | `server.py:111-127` | **High** | ~10 |
| 4 | **Unstructured, uncorrelated logging.** Plain-text `basicConfig`; 141 f-string `logger.*` calls; no request/correlation id; two disjoint sinks (`state_manager.logs` ring vs file logger). | `main.py:22`; `server.py:31`; `state_manager.py:168` | Med-High | ~30 |
| 5 | **Risk-state SQLite: no WAL, no busy timeout.** `circuit_breaker`/`drawdown` open a fresh `sqlite3.connect` per call, default `journal_mode=delete`, no `busy_timeout` → unhandled `database is locked`. | `circuit_breaker.py:73,109`; `drawdown.py:97,137` | Med-High | ~8 |
| 6 | **Per-tick thread-pool churn + broker-lock convoy.** `scan_all_modes` builds a new ≤32-thread pool per call; all N×3 fetches serialize on one process-wide `TrackedRLock`. | `orchestrator.py:973`; `mt5_client.py:38`; `broker_lock.py:42` | Med-High | ~6 |
| 7 | **Thread-per-connection server, no throttle.** `ThreadingHTTPServer`, no cap; SSE pins a thread for 60 s/client. | `server.py:13, 713-724` | Med | ~25 |
| 8 | **No security headers.** Missing CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`. CORS is origin-locked. `innerHTML` used 154× (escaped by convention only). | `server.py:1106-1121, 1130-1210` | Med | ~8 |
| 9 | **Stale duplicate DBs on disk** (root; `data/` is canonical). | `schema_version.py:5-7` | Med | ~5 |
| 10 | **Session defects.** `change_password` mutates only in-memory `_users`; allows 6 chars vs 8/12 elsewhere; revoked tokens never expire. | `remote_auth.py:97-98, 378, 399-403` | Med | ~12 |

**Verified-OK (not gaps):** MT5 hold is bounded-wait with owner attribution; `TimeoutGuard` detects/replaces wedged pools; loops carry stop signals; caches are key-bounded; copilot sessions are LRU-capped; SQL is parameterized (only f-string SQL is `PRAGMA user_version`/`table_info` with internal constants).

### Observability minimum viable (3 additions)

1. **Real `/health` (liveness) and `/ready` (dependency).** Wire the already-implemented `broker_lock_health()` and `TimeoutGuard.health()`. Return 503 when broker lock is held > N seconds, `TimeoutGuard.health()["wedged"]` is true, or `SELECT 1` fails. Drop `/api/diagnostics` from the public list (or reduce to `{status, services, timestamp}`).
2. **One correlation id per request, in structured logs.** A `ContextVar` set at the top of `do_GET`/`do_POST`, emitted on every log record via `LoggerAdapter`. Convert the 141 f-string calls on the money path to `extra={...}` so a failed trade is reconstructable from logs alone.
3. **Tiny `/api/metrics`** (Prometheus text or JSON): requests by `path/status`, broker-lock wait count + `BrokerLockBusy` count, guard stuck-workers gauge, DB write errors, process uptime. Counter sites already exist (`server.py:742, 1100`, `broker_lock.py:71`).

### Scalability minimum viable (what breaks first as N grows)

1. **The single global broker lock.** Every `fetch_multi_timeframe` and every order takes one process-wide `TrackedRLock` (`mt5_client.py:38`); `scan_all_modes` submits 3N tasks (`orchestrator.py:967`) that all queue behind it. Break point: more symbols/styles than the lock can drain per tick → linear latency growth, then `BrokerLockBusy` fires. **Fix:** batch/cache broker reads; move non-broker compute outside the lock.
2. **Thread-per-connection server + per-tick pool creation.** Break point: client count or request flood drives thread count and churn until thread/memory-bound. **Fix:** one shared bounded pool; explicit max-concurrency guard; per-IP throttle beyond login.

Secondary ceiling: SQLite single-writer. With `database.py` in WAL the history path is fine, but the risk-state DBs are in rollback-journal mode (gap #5) and will surface `database is locked` first.

---

## Proposed execution roadmap (phased, with sign-off gates)

Each phase ends with `pytest` green and the layout verifier (`tools/verify_ui_layout.js` + `tools/verify_mobile_dock.js`) green. Implementation halts at every `⛔` for user approval.

| Phase | Scope | Risk | Touches entry model? |
|---|---|---|---|
| **1 — Regress-free cleanup** | Delete 10 scratch + 6 root `test_*`; archive 4 root DBs + 7 winrate-profiles; add 3 security response headers (`X-Content-Type-Options`, `X-Frame-Options`, CSP); add basic JSON logging formatter | None | No |
| **2 — Observability + safety** | Real `/health` + `/ready`; drop `/api/diagnostics` from public; wire `broker_lock_health()` + `TimeoutGuard.health()`; WAL + busy_timeout on risk-state DBs; revoke-and-prune token expiry; min password length 12 | Low | No |
| **3 — Refactor reorg** | Move `timeout_guard.py` → `jarvis/common/`; remove 9 unused imports; collapse `jarvis.bat`/`JARVIS.cmd`/`HM_start.bat`/`HM_start.ps1`; demote `jarvis.py` duplication | Low | No |
| **4 — Scalability first cuts** | Hoist `ThreadPoolExecutor`; move non-broker compute outside the lock; per-IP request throttle; raise the metrics surface | Low–medium (touches request path) | No |
| `⛔` | **Approval gate.** Show: remaining gaps; updated count of pytest-green + verifier-green. | | |
| **5 — Entry-model hardening (backtested before live)** | Enforce stop floor ≥ 1.11×ATR (#1); recompute TP off floored risk (#2); add `exit_price`/`realized_pnl` columns (#3) | Med — needs backtest | **Yes — but only after backtest on the same instrument is run and DSR > 0.95 on effective n** |
| **6 — Architecture fixes (architecture-level changes only)** | Loopback auth bypass tightening (#3); CSE/security headers expansion; break `market⟲intelligence` and `intelligence⟲backtesting` cycles | Med-High | No |
| `⛔` | **Approval gate.** Show: entry-model backtest verdict (DSR, always-long delta); if NOT profitable → STOP here and revisit the model. | | |
| **7 — FVG standalone backtest** | The honest recipe in §C, run via `tools/deflated_sharpe_report.py` + `tools/p0_1_direction_audit.py`. If passes both bars → wire FVG as *additional* (not replacement) entry. | Backtest only. | **Only if §C verdict clears.** |

### What I will **not** do without explicit approval

- Any change that affects entry behaviour (Track 1 #1–#4) without a backtest on the same instrument showing non-negative effect.
- The loopback-auth-bypass tightening (Track 3 #3) — may break the live tunnel.
- Flipping `trading.use_calibrated_entry_policy` (Track 1 #5) — this is a *trading* decision.
- Wiring FVG as a replacement (Track 1 §C) — explicitly WAIT per the user's "evaluate before implementing" instruction.

---

## Execution log — phases 1–4 (committed `35f13bf`)

Approved scope was **phases 1–4 now, entry-model + FVG deferred**, and **keep the loopback auth bypass for now**. Everything below is merged and pushed; nothing touches the entry model.

| Phase | Planned | Done | Delta |
|---|---|---|---|
| **1 — Cleanup** | Delete 10 scratch + 6 root `test_*`; archive 4 root DBs + 7 winrate-profiles; 3 security headers | Deleted **16** root scripts (the 10 scratch + 6 `test_*`); moved 7 root DBs to `data/_archive_root/` (gitignored); **deleted** the 7 calibration snapshots outright rather than archiving them (~690 KB) — only `config/winrate_profiles.json` is ever loaded, and all 7 remain recoverable from git history. Security headers shipped with Phase 2. | Archiving became deleting: a copy was made first, then the duplicates were dropped so the commit adds no redundant bytes. |
| **2 — Observability + safety** | all items | `/health` + `/ready` (200/503, per-subsystem `broker_lock` / `guard` / `db`); `/api/diagnostics` dropped from the public allowlist; `X-Content-Type-Options`, `X-Frame-Options` and a CSP on every JSON, static and template response; token revocation with expiry pruning; **12-char password floor for ADMIN only**; WAL + `busy_timeout=5000` on both risk DBs | Password floor is admin-only: a global 12-char floor would have broken `tests/test_remote_auth.py:438` (10-char password) and `:445` (asserts "at least 6"). |
| **3 — Refactor reorg** | move `timeout_guard.py`, remove 9 unused imports, collapse wrappers | `timeout_guard.py` → new `jarvis/common/` (byte-identical, no shim, 6 import sites updated); 9 unused imports removed | **Wrapper consolidation NOT done** — `jarvis.bat` ≡ `JARVIS.cmd` (byte-identical) and `HM_start.bat` ≈ `HM_start.ps1` are daily-use entry points; deleting them needs your sign-off. |
| **4 — Scalability** | hoist pool; move compute outside lock; per-IP throttle; metrics | `ThreadPoolExecutor` hoisted to a module singleton (16 workers, `atexit` shutdown) | **"Move compute outside the lock" was correctly SKIPPED with evidence**: the heavy compute is *already* outside the broker lock — the lock is held only around native MT5 calls (`mt5_client.py:90,223,…`; `data_feed.py:360-366`) and `orchestrator.py` contains zero broker-lock references. Per-IP throttle and metrics surface deferred to phase 6. |

### Note on `get_remote_mobile_access.py`

One of the 16 deleted files **printed a hardcoded admin password**. It is gone from the working tree, but it remains in git history — **rotate that credential**.

## Test coverage added for this work

The mandate asked for tests covering the refactored behaviour *and* trade-entry logic. All new
files live in `tests/`, are hermetic (no MT5, no sockets, no real DBs), and are collected by the
default `pytest` run.

| File | Asserts |
|---|---|
| `tests/test_entry_geometry_invariants.py` | **Trade-entry logic.** Entry is the raw observed ask/bid — the written proof that entries are *not* snapped to support/resistance (§B). Stop side and sign for BUY and SELL. `risk_dist == abs(entry − sl_price)`, the regression guard for the ~7× risk blow-up where the sizer priced risk off a 0.7-pip stop while the post-fill re-anchor applied a floored 5-pip distance. TP side, RR self-consistency, and the refuse-don't-fabricate path for unobserved and negative prices. Two **characterisation** tests pin the known defect: the floor is `max(3 × spread, 0.10 × ATR)` today, and an `xfail` asserts the Phase 5 target of ≥ 1.11 × ATR so it flips to XPASS the moment the floor is raised. |
| `tests/test_health_endpoint.py` | `/health` and `/ready` are reachable unauthenticated, return 200 with `broker_lock` / `guard` / `db` / `ts`, degrade to 503 when a subsystem fails, and probe each dependency independently instead of raising. |
| `tests/test_security_headers.py` | `nosniff` and `X-Frame-Options: DENY` on JSON, static and template responses, and a CSP that permits the CDN hosts the dashboard actually fetches from. |
| `tests/test_remote_auth_revocation.py` | A revoked token is rejected; expired revocations and >1 h `_failed_attempts` are pruned; the ADMIN-only 12-char password floor, including the deliberate asymmetry that a non-admin may still use 6–11 chars. |
| `tests/test_risk_db_pragmas.py` | Both risk DBs apply `journal_mode=WAL` and `busy_timeout=5000` on *every* open — the defect was a one-time pragma in `_init_db` that never reached `_save_state`. |
| `tests/test_scan_executor_singleton.py` | The scan pool is a module-level `ThreadPoolExecutor` with `max_workers == 16`, and `scan_all_modes` submits to it rather than building a pool per call. |

## Test status today

| Check | Result |
|---|---|
| `pytest` | **3004 passed / 0 failed / 20 deselected** (junit `tests=3018 failures=0 errors=0 skipped=1`) |
| `tools/verify_ui_layout.js` | **345/345** |
| `tools/verify_mobile_dock.js` | **24/24** stable across 3 consecutive runs |
| Interaction smoke test | **ALL INTERACTIONS OK** stable across 3 consecutive runs |
| Desktop proof @1440px | 0 elements differ on all four market pages (with per-page substantive-comparison guard) |

---

## The one sentence to carry

The recurring losses have a **mechanical, fixable cause** (stop floor), but the **fundamental problem is that the entry signal has no measured edge** — and Track 1's own evaluation concludes that *no entry-model change can produce profitability until that verdict is overturned with evidence*. The honest next step is the standalone FVG backtest recipe in §C, run on the existing instruments, before anything else on the model is touched.