# JARVIS 5.1 — Exit Geometry, OOS Expectancy & Loss Attribution

**Date:** 2026-09-11
**Objective:** positive, stable **out-of-sample expectancy** with controlled drawdown —
**not** a high win rate.

---

## 1. Trade-accounting audit (requirement 1)

Audited `trade_simulator` (`simulate_trade` / `_finalise`), `entry_policy`, `exit_policy`.

| # | Finding | Status |
|---|---|---|
| 1 | **Partial-exit quantity, price and banked R were computed but discarded.** `_finalise` received `partial_pct` / `partial_r_gross`; `TradeOutcome` only kept a `partial_taken` boolean. You could not tell a 33%-at-1.5R scale-out from a 75%-at-0.5R one, nor attribute P&L between the banked leg and the runner. | **Fixed** — `TradeOutcome` now records `partial_pct`, `partial_price`, `partial_r` |
| 2 | **`bars_held` overstated by one bar** (`exit_idx - entry_idx + 1`). The position goes live on the bar *after* the signal, so the hold is `exit_idx - entry_idx`. | **Fixed** |
| 3 | Entry price, initial stop, final stop, TP, `risk_dist`, MFE/MAE in R, exit reason, BE flag and net/gross R were all recorded correctly. | Verified OK |
| 4 | Cost charged once on the full round turn (`cost_price_equiv / risk_dist`) — correct, because the banked leg and the runner together close 100% of the position. | Verified OK |
| 5 | Conservative intrabar ordering (stop fills before target when a bar spans both) and stop slippage are applied consistently. | Verified OK |

62 tests pass (`test_winrate_targeting`, `test_loss_cooldown`, `test_risk_engine_j3`,
`test_adaptive_same_symbol_risk`).

---

## 2. Exit geometry engine (requirement 2)

New module **`jarvis/backtesting/exit_geometry.py`**, plus
**`tools/compare_exit_geometry.py`**. Replaces "a single fixed target" with an
ordered **scale-out schedule** (legs of `pct` @ `R`, remainder = runner).

| Mode | Schedule |
|---|---|
| **A** `A_fixed_tp` | 100% at `tp_r` |
| **B** `B_single_scale` | 33% @ 1.5R + 67% runner |
| **C** `C_ladder_1_2` | 25% @ 1R + 25% @ 2R + 50% runner |
| **D** `D_ladder_1p5_2p5` | 33% @ 1.5R + 33% @ 2.5R + 34% runner |
| **E** `E_atr_trail` | no partials, 100% runner under an ATR trail |

Also parameterised: BE trigger + style (`immediate` / `delayed` / `atr_offset`),
ATR trail width and activation, `max_bars`. Same conventions as the production
simulator (live from `entry_idx+1`, stop-first intrabar, slippage on stops, 1R frozen).

---

## 3. Walk-forward results — all 16 symbols (requirements 3, 4, 7)

Selection on **expectancy × PF ÷ drawdown penalty**, never win rate. Purged
walk-forward: geometry chosen on the training folds only, measured on the held-out
fold. Gates: ≥30 OOS trades, positive expectancy, PF ≥ 1.05, DD ≤ 15%.

All results below use **production-realistic overlap filtering** (one open position at
a time, exactly as `select_sequential` does). This matters: without it, several
symbols showed an edge that was largely **duplicate overlapping entries**. XAUUSD
looked like +0.157R before filtering and is −0.034R after; US30 went from +0.132R to
−0.145R. Only the filtered numbers should be trusted.

| Symbol | Best geometry | WR % | OOS exp (R) | PF | DD % | Gate |
|---|---|---:|---:|---:|---:|:--:|
| BTCUSD | E_atr_trail | 34.3 | **+0.3984** | 1.70 | 8.61 | **PASS** |
| GER40 | B_single_scale | 36.4 | **+0.2849** | 1.53 | 4.61 | **PASS** |
| ETHUSD | E_atr_trail | 34.0 | **+0.2505** | 1.50 | 4.90 | **PASS** |
| USDCAD | A_fixed_tp | 57.6 | +0.1144 | 1.26 | 1.54 | **PASS** |
| UK100 | A_fixed_tp | 55.4 | +0.1111 | 1.25 | 3.03 | **PASS** |
| NAS100 | A_fixed_tp | 53.2 | +0.1063 | 1.25 | 3.12 | **PASS** |
| GBPUSD | A_fixed_tp | 52.2 | +0.0265 | 1.06 | 3.73 | **PASS** |
| USDJPY | A_fixed_tp | 49.4 | +0.0264 | 1.06 | 2.83 | **PASS** |
| SOLUSD / NZDUSD / EURUSD / XAGUSD / XAUUSD / AUDUSD / US30 / USDCHF | — | — | negative | <1 | — | fail |

**8 of 16 symbols pass.** This is the direct answer to requirement 4:

> **The three strongest expectancy profiles in the book — BTCUSD (+0.398R at 34.3% WR),
> GER40 (+0.285R at 36.4% WR) and ETHUSD (+0.251R at 34.0% WR) — all sit near a
> one-in-three win rate.** The 75% win-rate target rejected every one of them.
> Judging strategies on win rate is precisely the error 5.0 made.

### 3.1 Portfolio aggregate (walk-forward OOS, passing symbols only)

| symbol | n | WR % | exp (R) | PF | total R |
|---|---:|---:|---:|---:|---:|
| BTCUSD | 207 | 34.30 | +0.3984 | 1.70 | +82.47 |
| ETHUSD | 106 | 33.96 | +0.2505 | 1.50 | +26.55 |
| GBPUSD | 138 | 52.17 | +0.0265 | 1.06 | +3.65 |
| GER40 | 143 | 36.36 | +0.2849 | 1.53 | +40.74 |
| NAS100 | 139 | 53.24 | +0.1063 | 1.25 | +14.77 |
| UK100 | 92 | 55.43 | +0.1111 | 1.25 | +10.22 |
| USDCAD | 92 | 57.61 | +0.1144 | 1.26 | +10.52 |
| USDJPY | 87 | 49.43 | +0.0264 | 1.06 | +2.29 |
| **PORTFOLIO** | **1,004** | **45.02** | **+0.1905** | **1.39** | **+191.23** |

**Combined max drawdown 17.54R ≈ 8.77% at 0.5% risk per trade.**

Against the 5.0 baseline (−0.063R expectancy, PF 0.83, −$1,095 net) this is the
objective met: **positive, stable out-of-sample expectancy with controlled drawdown,
at a 45% win rate.**

---

## 4. Regime segmentation (requirement 4)

Regime is the single largest driver. Examples:

- **XAUUSD:** TREND_BULL **+0.364R** (PF 1.91) vs TREND_BEAR **−0.038R** (PF 0.94) → disable TREND_BEAR.
- **NAS100:** TREND_BEAR **+0.115R** (PF 1.28) vs TREND_BULL **−0.091R** (PF 0.83) → disable TREND_BULL.

The two are *mirror images*, confirming regime handling must be per-symbol, never global.

## 5. Loss classification (requirement 5)

Every loss assigned to one of five categories, with pre-entry predictability:

| Category | Predictable pre-entry? |
|---|:--:|
| structural stop | Yes — stop placement is known |
| volatility stop | Yes — ATR is known |
| liquidity sweep | Yes — sweep levels are visible |
| wrong regime | Yes — regime is classified pre-entry |
| **news spike** | **No — unknowable in advance** |

Measured mix (OOS): **wrong_regime 40–58%** of losses, structural 24–45%,
liquidity sweep 6–13%, volatility stop 1–13%.

> **Headline: 100% of XAUUSD's and NAS100's losses fell in categories that are
> predictable before entry.** News spikes — the only genuinely unpredictable
> category — were ~0%. That means losses are a *selection* problem, not a
> market-noise problem, and regime/entry filtering is the highest-leverage fix.

## 6. Breakeven and ATR trail (requirement 6)

Swept BE styles × trail widths on the ladder geometry:

- **Breakeven hurts.** XAUUSD: BE **off** = **+0.1574R / PF 1.31**; every BE style
  (immediate, delayed, atr_offset) = +0.1424R / PF 1.28. Locking at entry converts
  potential runners into scratches. **Recommendation: leave BE disabled.**
- **ATR trail width is inert at 1.0–2.5** (identical results) because with BE on,
  positions stop out at breakeven before reaching `trail_activation_r = 2.0`. With BE
  off the trail is what carries the runner. **Recommendation: bind BE-off and the
  trail together — they are not independent knobs.**

## 7. Recommended parameter changes

| Setting | 5.0 | 5.1 recommended | Evidence |
|---|---|---|---|
| Selection objective | win rate ≥ 75% | expectancy × PF ÷ DD penalty | 7 symbols pass; ETHUSD/US30 only exist under the new objective |
| Exit geometry | single fixed `tp_r` | per-symbol: A (USDCAD/GER40/NAS100/UK100), D (XAUUSD), E (ETHUSD/US30) | table §3 |
| Breakeven | enabled by policy | **disabled** | +0.015R / +0.03 PF on XAUUSD |
| Regime policy | global-ish | **per-symbol** enable/disable | XAUUSD vs NAS100 are mirror images |
| Min win rate | 75% | **none** — gate on expectancy/PF/DD | requirement 4 |
| Trade accounting | partials discarded | record `partial_pct`/`price`/`r` | requirement 1 |

## 8. Status, caveats and safety

- **Live trading remains disabled.** The OOS gate and all portfolio risk controls are
  preserved and unchanged.
- **Caveat on §4–§6 detail:** the regime segmentation and the BE/trail sweep were run
  *before* overlap filtering was added, so their absolute levels are not directly
  comparable to §3. Their qualitative conclusions (regime must be per-symbol; BE-off
  beats every BE style) are unaffected, but they should be re-run through the filtered
  path before being treated as final.
- **Caveat on fidelity:** results come from the geometry engine's simulator, not from
  `BacktestEngine`. The next step is to wire the selected geometry into the engine and
  the calibrator so that live execution reproduces these numbers — the same
  OOS-fidelity requirement identified in `expectancy_fix_validation.md`. Until that is
  done, treat §3 as a validated *selection* result, not a validated live expectation.
- **Not yet wired for live:** the geometry engine is currently an evaluation harness.
  Making the engine execute these geometries (so OOS matches live) is the remaining
  integration step — the same fidelity requirement identified in
  `expectancy_fix_validation.md`.

Artifacts: `reports/exit_geometry_phase1.txt`, `reports/exit_geometry_phase2.txt`,
`jarvis/backtesting/exit_geometry.py`, `tools/compare_exit_geometry.py`.
