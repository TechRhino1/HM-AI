# Remote Divergence: the Upstream BTCUSD Commit, Audited

**Situation.** `origin/main` is no longer where it was when this session started. It has advanced to
`0f630a3` — *"Optimize BTCUSD trading parameters and geometry on MT5 H1 data (66.8% WR, +1.22 PF)"*
by TechRhino1, dated 2026-09-12 20:19 IST. Its parent is `ab56d29`, which is the same commit this
session branched from.

**The histories have diverged.** The remote holds one commit we do not have; we hold five it does
not:

```
origin/main :  ... — ab56d29 — 0f630a3            (their BTCUSD optimization)
HEAD        :  ... — ab56d29 — 76c0289 — d50a4ee — b1ec997 — a113026 — 0ba3b3a
```

A plain `git push` will be rejected as non-fast-forward. This needs a merge or a rebase, which is a
history-shaping decision I have not made on your behalf.

**The conflict is real and narrow.** Their commit touches four files, and one of them is
`config/winrate_profiles.json` — the same file I have regenerated three times this session:

| File | Their change | Conflicts with us? |
|---|---|---|
| `config/winrate_profiles.json` | BTCUSD profile hand-edited | **Yes** — we rewrote all 16 profiles |
| `jarvis/analysts/devil_advocate.py` | 2 lines | No |
| `jarvis/intelligence/symbol_profile_config.py` | 30 lines | No |
| `reports/btcusd_baseline_audit.json` | new, 1,217 lines | No |

## 1. What they changed

```diff
  "geometry": {
-   "tp_r": 2.0,          "max_bars": 48,   "min_score": 0.5
+   "tp_r": 0.6,          "max_bars": 36,   "min_score": 0.55
  "geometry_mode": "E_atr_trail"  ->  "A_fixed_tp"
  TREND_BULL regime:  enabled  ->  disabled
```

`trail_atr` stays at `1.5`, but with `tp_r = 0.6` the trail activates at 2.0R and is therefore
**inert** — a trade targeting 0.6R exits long before it engages. So this change also, incidentally,
sidesteps the engine/simulator trail divergence fixed in `a113026`: at `tp_r = 2.0` with a live trail
the two paths disagreed, at `tp_r = 0.6` they cannot.

## 2. It is built on a stale base

Their `config/winrate_profiles.json` carries `"generated_utc": "2026-09-11T17:23:29"`. That is the
**pre-fix 16-symbol calibration** — produced before the symbol-registry repair, before the
policy-fitted-on-train-folds fix, and before the reachability guard. Only the BTCUSD entry was then
hand-edited on top. The other 15 profiles in their file are the stale ones.

Two internal inconsistencies in the committed entry:

* **`notes` contradicts `geometry`.** The notes still read *"Deployed
  tp0.25_beoff_pcoffx0.5_troff@2_mb48_s0.60528 … OOS 73.6% on 140 trades (**−0.017R**, PF 0.93)"* —
  a different geometry (tp0.25, 48 bars, trail off) with a **negative** out-of-sample expectancy. The
  geometry block says tp0.6 / 36 bars / trail 1.5. The two were never reconciled.
* **The profile flags itself as unvalidated.** `gate_fail_reason: "realised_trades=13<30"` and
  `target_met_oos: false`.

Note also that their own notes record the pattern this session diagnosed: **73.6% win rate with
−0.017 R** — a high win rate and a negative expectancy, simultaneously.

## 3. Verification: I ran their profile through the corrected engine

Installed their BTCUSD profile alongside my other 15, ran the production engine on the 95-day window:

| | Their claim | Measured on corrected code |
|---|---:|---:|
| Win rate | 66.8% | **66.1%** |
| Profit factor | +1.22 | **1.14** |
| Trades | — | 62 |
| Expectancy | — | +0.058 R |
| Net | — | +$102.89 |
| Max drawdown | — | 1.24% |
| **Uniqueness-weighted expectancy** | — | **−0.1262 R** |

**Their win rate reproduces** (66.1% vs 66.8%) — the work is genuine, not fabricated. The profit
factor is a little optimistic (1.14 vs 1.22).

**But the edge does not survive overlap weighting: −0.1262 R.** Once trades that were open at the
same time are down-weighted, the result is negative. The +0.058 R headline rests on clustered,
simultaneous positions rather than on independent bets.

## 4. Credit where it is due

Their change is **directionally correct**, and lands close to what my own corrected calibration chose
independently:

* They moved `tp_r` from **2.0 → 0.6**. Break-even at `tp_r = 0.6` is `1/1.6` = **62.5%**, and they
  achieve 66.1% — so the geometry is genuinely *above* break-even. That is the opposite of the
  unreachable-geometry trap: it is a fix in the right direction.
* My margin-0.17 calibration selected **BTCUSD `tp_r = 0.60`** as well. Two independent routes
  converged on the same value.
* `tp_r = 0.6` clears the reachability floor of 0.3333, so this change would survive the guard.

**BTCUSD's spec was not altered by the registry repair** — I checked the diff; the fix touched
GER40, UK100, XAGUSD, NAS100, US30, US500, SOLUSD, ETHUSD, NZDUSD, USDCAD and USDCHF, not BTCUSD. So
their BTCUSD tuning is **not** invalidated by that fix.

## 5. What needs deciding

**History.** Merge `origin/main` into `HEAD`, or rebase our five commits onto `0f630a3`? I have not
done either. Rebase gives a linear history but rewrites five commits that are not yet published;
merge is safer and preserves both. Either way the `config/winrate_profiles.json` conflict must be
resolved by hand.

**The profile file.** Their commit replaces it with a stale 16-symbol calibration plus one hand-edited
symbol. Mine is a single consistent 16-symbol calibration run on the corrected code. My
recommendation is to keep the regenerated file and re-apply their BTCUSD geometry as a deliberate,
recorded change if you want it — but note the uniqueness-weighted expectancy above before you do.
Their BTCUSD is better on the headline (+$102.89) than what my calibration produced (BTCUSD OOS
−0.0159 R, i.e. refused), and worse on the overlap-adjusted measure. It is a real trade-off, not a
clear win, and it is your call.

## 6. Method

* Their profile extracted read-only via `git show 0f630a3:config/winrate_profiles.json`; the working
  tree was never left on their config.
* Measured with `tools/run_3month_backtest.py --symbols BTCUSD` on the corrected code, 95-day real
  MT5 H1 window, $10,000 at 0.5% risk. The 16-symbol portfolio report was restored from git
  afterwards.
* One window, 62 trades. The win-rate comparison is meaningful; the profit-factor gap (1.14 vs 1.22)
  is within what one window can move.
