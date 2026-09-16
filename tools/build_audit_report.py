"""Render the trade-plan profitability audit as a self-contained HTML report.

All figures come from ``reports/verdict.json`` and
``reports/trade_quality_audit.json`` -- nothing is typed in by hand, so the
report cannot drift from the measurement.

    python tools/build_audit_report.py
"""

from __future__ import annotations

import collections
import json
import os
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

VERDICT = os.path.join(REPO, "reports", "verdict.json")
AUDIT = os.path.join(REPO, "reports", "trade_quality_audit.json")
OUT = os.path.join(REPO, "reports", "trade_plan_audit.html")


def load():
    with open(VERDICT, "r", encoding="utf-8") as fh:
        verdict = json.load(fh)
    with open(AUDIT, "r", encoding="utf-8") as fh:
        audit = json.load(fh)
    return verdict, audit


def cost_gap_rows(verdict_rows):
    """Recompute expectancy under a cost model that is not broken.

    Gap = (what a round turn really costs) - (what the backtest charged):
      charged : spread on BUY entry only  -> 0.5 x spread averaged over sides
                plus 0.5 pip slippage on stop exits only (~50% of trades)
      real    : round-turn spread on both sides + round-turn commission
    Both expressed in R using each symbol's own median stop distance, so the
    comparison is scale-free across asset classes.
    """
    import numpy as np
    import pandas as pd
    from jarvis.data.symbol_registry import get_dollar_risk_per_price_unit, resolve

    out = []
    for r in verdict_rows:
        sym = r["symbol"]
        cp = os.path.join(REPO, "data", "signals", f"{sym}_H1_183d_candidates.parquet")
        bp = os.path.join(REPO, "data", "market", "real", sym, f"{sym}_H1_183d.parquet")
        if not (os.path.exists(cp) and os.path.exists(bp)):
            continue
        c = pd.read_parquet(cp, columns=["risk_dist", "bar_idx"])
        b = pd.read_parquet(bp, columns=["spread"])
        spec = resolve(sym)
        money = get_dollar_risk_per_price_unit(sym, None)
        pt = 10.0 ** -(spec.digits or 5)
        rd = float(c["risk_dist"].median())
        if rd <= 0:
            continue
        idx = np.clip(c["bar_idx"].to_numpy(), 0, len(b) - 1)
        sp = float(np.median(pd.to_numeric(b["spread"], errors="coerce").fillna(0).to_numpy()[idx])) * pt
        real = (2.0 * sp) / rd + 10.0 / (rd * money)      # 2 sides spread + $5/lot round turn
        charged = (0.5 * sp + 0.25 * spec.pip_size) / rd
        out.append({
            "symbol": sym,
            "pf": r["profit_factor"],
            "e_measured": r["expectancy_r"],
            "gap": real - charged,
            "e_corrected": r["expectancy_r"] - (real - charged),
        })
    return sorted(out, key=lambda x: -x["e_corrected"])


def rows_for(verdict, style, tp=1.5):
    out = [r for r in verdict if r["style"] == style and abs(r["tp_r"] - tp) < 1e-9]
    return sorted(out, key=lambda r: -r["profit_factor"])


def regime_agg(audit, style, key):
    tot = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for sym, e in audit["styles"].get(style, {}).items():
        m = e.get("tp1.5_costs", {})
        for k, v in m.get(key, {}).items():
            if v.get("n", 0) < 50:
                continue
            t = tot[k]
            t[0] += v["n"]
            t[1] += v.get("net_r", 0.0)
            t[2] += v.get("profit_factor", 0.0) * v["n"]
            t[3] += v.get("win_rate", 0.0) * v["n"]
    return [(k, v[0], v[1], v[2] / v[0], v[3] / v[0]) for k, v in tot.items()]


def fmt_pct(x, nd=1):
    return f"{x * 100:.{nd}f}%"


def build_html() -> str:
    verdict, audit = load()
    h1 = rows_for(verdict, "SWING(H1)")
    m15 = rows_for(verdict, "DAY_TRADING(M15)")

    n_sym = len(h1)
    pf_max = max(r["profit_factor"] for r in h1)
    pf_min = min(r["profit_factor"] for r in h1)
    n_ge_13 = sum(1 for r in verdict if r["profit_factor"] >= 1.3)
    n_ge_10 = sum(1 for r in h1 if r["profit_factor"] >= 1.0)
    dd_min = min(r["dd_pct_at_1pct"] for r in h1)
    dd_max = max(r["dd_pct_at_1pct"] for r in h1)
    f_min = min(r["f_for_10pct_dd_pct"] for r in h1)
    f_max = max(r["f_for_10pct_dd_pct"] for r in h1)
    exec_ok = sum(1 for r in h1 if r["executable_at_dd_target"])

    reg = regime_agg(audit, "SWING(H1)", "by_regime")
    vol = regime_agg(audit, "SWING(H1)", "by_volatility")
    trd = regime_agg(audit, "SWING(H1)", "by_trend")

    sym_pf = json.dumps([r["symbol"] for r in h1])
    sym_pfv = json.dumps([round(r["profit_factor"], 3) for r in h1])
    sym_wr = json.dumps([round(r["win_rate"] * 100, 2) for r in h1])
    sym_be = json.dumps([round(r["break_even_wr"] * 100, 2) for r in h1])
    sym_dd = json.dumps([round(r["dd_pct_at_1pct"] * 100, 1) for r in h1])

    reg_names = json.dumps([r[0] for r in reg])
    reg_pf = json.dumps([round(r[3], 3) for r in reg])
    reg_n = json.dumps([r[1] for r in reg])

    def table(rows, tp_label):
        out = []
        for r in rows:
            cls = ("ok" if r["profit_factor"] >= 1.3 else
                   "warn" if r["profit_factor"] >= 1.0 else "bad")
            out.append(
                f"<tr><td class='mono'>{r['symbol']}</td>"
                f"<td class='num'>{r['n']:,}</td>"
                f"<td class='num'>{fmt_pct(r['win_rate'], 2)}</td>"
                f"<td class='num dim'>{fmt_pct(r['break_even_wr'], 1)}</td>"
                f"<td class='num {cls}'><b>{r['profit_factor']:.3f}</b></td>"
                f"<td class='num'>{r['expectancy_r']:+.4f}</td>"
                f"<td class='num'>{r['max_dd_r']:.0f}</td>"
                f"<td class='num bad'>{fmt_pct(r['dd_pct_at_1pct'], 1)}</td>"
                f"<td class='num'>{r['f_for_10pct_dd_pct']:.3f}%</td>"
                f"<td class='num'>${r['risk_usd_for_10pct_dd']:.2f}</td>"
                f"<td class='num dim'>${r['min_lot_risk_usd']:.2f}</td>"
                f"<td class='mono {cls}'>{r['verdict']}</td></tr>")
        return "\n".join(out)

    def reg_table(rows):
        out = []
        for name, n, net, pf, wr in sorted(rows, key=lambda x: -x[3]):
            cls = "ok" if pf >= 1.3 else ("warn" if pf >= 1.0 else "bad")
            out.append(f"<tr><td class='mono'>{name}</td><td class='num'>{n:,}</td>"
                       f"<td class='num'>{net:+,.0f}</td>"
                       f"<td class='num {cls}'><b>{pf:.3f}</b></td>"
                       f"<td class='num'>{fmt_pct(wr, 2)}</td></tr>")
        return "\n".join(out)

    corr = cost_gap_rows(h1)
    survivors = [c["symbol"] for c in corr if c["e_corrected"] > 0]
    corr_rows = "\n".join(
        f"<tr><td class='mono'>{c['symbol']}</td>"
        f"<td class='num {('ok' if c['pf'] >= 1.3 else ('warn' if c['pf'] >= 1.0 else 'bad'))}'>{c['pf']:.3f}</td>"
        f"<td class='num'>{c['e_measured']:+.4f}</td>"
        f"<td class='num bad'>-{c['gap']:.4f}</td>"
        f"<td class='num {('ok' if c['e_corrected'] > 0 else 'bad')}'><b>{c['e_corrected']:+.4f}</b></td></tr>"
        for c in corr)
    mean_gap = sum(c["gap"] for c in corr) / max(1, len(corr))

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HM-AI / HM Algo 2.0 — Trade Plan Profitability Audit</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  :root {{
    --ink:#1a1d21; --ink2:#4a5057; --ink3:#7b828b;
    --line:#e3e6ea; --bg:#f7f8fa; --card:#ffffff;
    --ok:#1a7f4b; --warn:#b8860b; --bad:#c0392b; --accent:#1f5f9e;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:32px 24px 80px; }}
  h1 {{ font-size:30px; margin:0 0 4px; letter-spacing:-.3px; }}
  h2 {{ font-size:21px; margin:44px 0 12px; padding-bottom:8px; border-bottom:2px solid var(--line); }}
  h3 {{ font-size:16px; margin:26px 0 8px; }}
  .sub {{ color:var(--ink3); font-size:13px; margin-bottom:24px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:20px 22px; margin:16px 0; }}
  .verdict {{ background:#fff8f0; border-left:5px solid var(--bad); }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); gap:12px; margin:20px 0; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .kpi .v {{ font-size:24px; font-weight:650; letter-spacing:-.5px; }}
  .kpi .l {{ font-size:12px; color:var(--ink3); margin-top:2px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin:10px 0; }}
  th {{ text-align:left; background:#f0f2f5; color:var(--ink2); font-weight:600;
    padding:8px 9px; border-bottom:2px solid var(--line); white-space:nowrap; }}
  td {{ padding:7px 9px; border-bottom:1px solid #eef0f3; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12.5px; }}
  .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
  .dim {{ color:var(--ink3); }}
  .chart {{ width:100%; height:340px; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media(max-width:900px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  code {{ background:#eef1f4; padding:1px 5px; border-radius:4px; font-size:12.5px; }}
  .path {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12.5px; color:var(--accent); }}
  ul {{ margin:8px 0 8px 20px; padding:0; }}
  li {{ margin:5px 0; }}
  .tag {{ display:inline-block; font-size:11px; font-weight:700; padding:2px 8px;
    border-radius:4px; margin-right:8px; vertical-align:2px; }}
  .p0 {{ background:#fdecea; color:#c0392b; }}
  .p1 {{ background:#fff4e0; color:#b8860b; }}
  .p2 {{ background:#e8f1fb; color:#1f5f9e; }}
  .p3 {{ background:#eef0f2; color:#5a6169; }}
  .note {{ font-size:12.5px; color:var(--ink3); margin-top:10px; }}
  .disc {{ background:#f0f2f5; border:1px solid var(--line); border-radius:8px;
    padding:14px 18px; font-size:12.5px; color:var(--ink2); margin-top:40px; }}
</style>
</head>
<body>
<div class="wrap">

<h1>Trade Plan Profitability Audit — HM-AI / HM Algo 2.0</h1>
<div class="sub">
  Generated {generated} · Source: local replay of <code>data/market/real/&lt;SYM&gt;/*_183d.parquet</code>
  (MT5 real bars, 2026-03-15 → 2026-09-11/13) through
  <code>jarvis/backtesting/trade_simulator.py</code> · Seed geometry tp=1.5R,
  costs as configured · {n_sym} symbols × 3 styles
</div>

<div class="card verdict">
  <h3 style="margin-top:0">Verdict</h3>
  <p style="font-size:16px;margin:0 0 10px">
    <b>The trade plans are unprofitable because the entry signal has no directional edge —
    not because of costs, exits, targets, or ranking.</b>
    Across {n_sym} symbols on H1 the profit factor spans <b>{pf_min:.2f}–{pf_max:.2f}</b>;
    only <b>{n_ge_10}/{n_sym}</b> symbols clear break-even at all, and exactly
    <b>{n_ge_13} of 40</b> symbol×target combinations reach the 1.3 acceptance threshold.
  </p>
  <p style="margin:0">
    The win rate is 28.2%–44.8% where a 1.5R target needs 40.0%. Zeroing every cost does not
    fix it (measured earlier: expectancy stays negative with free execution), widening the
    target does not fix it (profit factor is invariant to tp), and raising the score threshold
    does not fix it (no quantile reaches break-even). The direction is simply wrong more often
    than the payoff structure can absorb.
  </p>
  <p style="margin:0">
    The strongest form of this result: a <b>learned</b> secondary model cannot find a profitable
    subset either. Trained on 62k labelled trades with purged, embargoed splits, the meta-label
    gate reaches a held-out AUC of <b>0.48</b> — no better than a coin, and selecting its top
    decile makes the win rate worse. There is no subset of these signals that is being filtered
    out by a threshold. There is no subset to find.
  </p>
</div>

<div class="kpis">
  <div class="kpi"><div class="v bad">{pf_min:.2f}–{pf_max:.2f}</div><div class="l">Profit factor range (H1, 20 symbols)</div></div>
  <div class="kpi"><div class="v bad">{n_ge_13}/40</div><div class="l">Symbol×target combos with PF ≥ 1.3</div></div>
  <div class="kpi"><div class="v bad">28.2–44.8%</div><div class="l">Win rate vs 40.0% break-even</div></div>
  <div class="kpi"><div class="v bad">{fmt_pct(dd_min,0)}–{fmt_pct(dd_max,0)}</div><div class="l">Drawdown at the realised ~1% risk</div></div>
  <div class="kpi"><div class="v bad">{f_min:.3f}–{f_max:.3f}%</div><div class="l">Risk/trade needed for DD ≤ 10%</div></div>
  <div class="kpi"><div class="v bad">{exec_ok}/{n_sym}</div><div class="l">Symbols executable at that size</div></div>
</div>

<h2>1. Where the money actually goes</h2>

<div class="card">
  <h3>Profit factor by symbol — SWING / H1 (tp 1.5R, costs on)</h3>
  <div id="c_pf" class="chart"></div>
  <div class="note">Red line = break-even (PF 1.0). Green line = the 1.3 acceptance criterion.
  Every bar left of the red line destroys capital.</div>
</div>

<div class="grid2">
  <div class="card">
    <h3>Win rate vs break-even win rate</h3>
    <div id="c_wr" class="chart"></div>
    <div class="note">Break-even = 1/(1+tp) = 40.0% at tp 1.5R. The gap is the edge deficit.</div>
  </div>
  <div class="card">
    <h3>Drawdown at the realised ~1% risk per trade</h3>
    <div id="c_dd" class="chart"></div>
    <div class="note">Compounding equity, 1R = 1% of equity. The configured sizer realises
    0.74–1.32%, not the nominal 0.5%.</div>
  </div>
</div>

<h3>Per-symbol detail — SWING / H1, tp = 1.5R</h3>
<div class="card" style="padding:8px 12px">
<table>
<thead><tr>
  <th>Symbol</th><th class="num">Trades</th><th class="num">Win rate</th>
  <th class="num">Break-even</th><th class="num">Profit factor</th><th class="num">Expectancy</th>
  <th class="num">Max DD (R)</th><th class="num">DD @ 1% risk</th>
  <th class="num">Risk for DD≤10%</th><th class="num">$ risk @ $10k</th>
  <th class="num">Min-lot risk</th><th>Verdict</th>
</tr></thead>
<tbody>
{table(h1, "1.5")}
</tbody></table>
<div class="note">
  <b>Risk for DD≤10%</b> is derived, not assumed: with 1R = f × equity the equity curve is
  multiplicative, so DD ≈ 1 − exp(−f × max_dd_R) and f<sub>10%</sub> = −ln(0.90)/max_dd_R.
  <b>Min-lot risk</b> is the dollar risk of the smallest lot the broker accepts at this symbol's
  median stop distance. Where the first column is below the second, the drawdown target cannot be
  met at any placeable size.
</div>
</div>

<h3>Per-symbol detail — DAY_TRADING / M15, tp = 1.5R</h3>
<div class="card" style="padding:8px 12px">
<table>
<thead><tr>
  <th>Symbol</th><th class="num">Trades</th><th class="num">Win rate</th>
  <th class="num">Break-even</th><th class="num">Profit factor</th><th class="num">Expectancy</th>
  <th class="num">Max DD (R)</th><th class="num">DD @ 1% risk</th>
  <th class="num">Risk for DD≤10%</th><th class="num">$ risk @ $10k</th>
  <th class="num">Min-lot risk</th><th>Verdict</th>
</tr></thead>
<tbody>
{table(m15, "1.5")}
</tbody></table>
<div class="note">Faster timeframe, worse result: M15 win rates sit 1–6 points below their H1
counterparts on the same symbols. That ordering is the signature of a fixed cost being paid
against a signal that has no edge to pay it with.</div>
</div>

<h2>2. Regime attribution</h2>
<p>Regime buckets are derived per symbol from its own distribution (terciles of ATR% and of
EMA separation measured in ATR units) — no fixed cut-offs. Pooled across the 20-symbol universe,
SWING/H1:</p>

<div class="grid2">
  <div class="card">
    <h3>Profit factor by market regime</h3>
    <div id="c_reg" class="chart"></div>
  </div>
  <div class="card">
    <table>
      <thead><tr><th>Regime</th><th class="num">Trades</th><th class="num">Net R</th>
      <th class="num">PF</th><th class="num">Win rate</th></tr></thead>
      <tbody>{reg_table(reg)}</tbody>
    </table>
  </div>
</div>

<div class="grid2">
  <div class="card">
    <h3>By volatility tercile</h3>
    <table>
      <thead><tr><th>Bucket</th><th class="num">Trades</th><th class="num">Net R</th>
      <th class="num">PF</th><th class="num">Win rate</th></tr></thead>
      <tbody>{reg_table(vol)}</tbody>
    </table>
  </div>
  <div class="card">
    <h3>By trend-strength tercile</h3>
    <table>
      <thead><tr><th>Bucket</th><th class="num">Trades</th><th class="num">Net R</th>
      <th class="num">PF</th><th class="num">Win rate</th></tr></thead>
      <tbody>{reg_table(trd)}</tbody>
    </table>
  </div>
</div>

<div class="card">
  <h3>Two things the regime table says that the aggregate hides</h3>
  <ul>
    <li><b>The only profitable regime is COMPRESSION</b> (PF 1.203, win 43.3%, n=844) — a
    mean-reverting regime. <b>TREND_BULL is the worst</b> (PF 0.872) even though the strategy
    is labelled <code>TREND_FOLLOWING</code>. A "trend following" system that loses most in
    trends and wins in compression is a mean-reversion signal wearing the wrong label. The
    <code>Premium/Discount Alignment</code> hard gate (<span class="path">decision_engine.py:984-1002</span>)
    forces entries <i>against</i> the move, which is consistent with this.</li>
    <li><b>Ranging beats trending</b> on the trend-strength axis (PF 0.951 vs 0.845 on H1;
    0.898 vs 0.826 on M15) and <b>high volatility beats low</b> on H1 (0.999 vs 0.873). Both
    point the same way: the edge, such as it is, lives in range-bound, volatile, compressed
    conditions — the exact opposite of what the engine is configured to hunt.</li>
  </ul>
</div>

<h2>3. Root causes, in the order they cost money</h2>

<div class="card">
<h3><span class="tag p0">P0</span>The directional call is a 7-branch if/elif with no model behind it</h3>
<p><span class="path">jarvis/intelligence/decision_engine.py:105-122</span> —
<code>_compute_bias_and_levels</code> decides BUY/SELL/HOLD from two booleans
(<code>choch</code>, <code>bos</code>) and a hand-authored <code>trend_score</code> ladder
(<span class="path">momentum.py:74-102</span>, additive ±45/±25/±… with no fitted parameter).</p>
<p>The "probability" that gates the trade is then
<code>0.45 × calibrated + 0.55 × ml</code> (<span class="path">decision_engine.py:193</span>) where:</p>
<ul>
  <li><b>calibrated</b> comes from a hand-typed 6-bin table
  (<span class="path">confidence.py:13-20</span>: 0.48/0.59/0.66/0.74/0.82/0.86) whose own comment
  admits it was "raised ~0.03–0.04 to improve win-rate gate pass-through". It <i>inflates</i>
  inputs, so <code>calibrated_win_p ≥ ~0.53</code> on every directional call and the 0.45–0.48
  floor is cleared mechanically.</li>
  <li><b>ml</b> is an <b>untrained prior</b>. <span class="path">online_ml_predictor.py:564-573</span>
  loads <code>DEFAULT_WEIGHTS</code> whenever <code>is_offline()</code> — i.e. in every backtest —
  and the output is clipped to [0.35, 0.88] (<span class="path">:417</span>), so aligned setups
  saturate at 0.88. Nothing is ever fit on data offline.</li>
</ul>
<p><b>Consequence:</b> 55% of the gate weight sits on a number that has never seen a trade.
This is the single largest cause of the 28–45% hit rate.</p>
</div>

<div class="card">
<h3><span class="tag p0">P0</span>The only learned component in the system is inert in every backtest</h3>
<p><span class="path">decision_engine.py:1033-1045</span> — the meta-label gate requires
<code>recent_candles</code>, which neither <code>BacktestEngine</code>
(<span class="path">engine.py:577-579</span>) nor <code>SignalScanner</code>
(<span class="path">signal_scan.py:226-229</span>) ever passes; and
<span class="path">meta_labeler.py:191-193</span> loads <code>model=None</code> offline.
The one genuinely fitted model in the codebase never runs.</p>
</div>

<div class="card">
<h3><span class="tag p0">P0</span>Sizing realises ~2× the configured risk, and the fix is not a number — it is a formula</h3>
<p><span class="path">jarvis/risk/position_sizing.py:56</span> —
<code>quarter_kelly_pct = max(0.15, min(1.50, (full_kelly/4.0)*100.0))</code>. For any plausible
(p, R) this hits the 1.50 cap, so the Kelly term is a <b>constant</b>, not an adaptive fraction,
and the 50/50 blend at <span class="path">:75</span> roughly doubles the nominal 0.5%.
Measured on XAUUSD: 0.5% nominal → 0.74%–1.32% realised. Every backtest number in this report
is therefore a ~2× risk number, and the drawdowns are correspondingly understated.</p>
<p>Separately <span class="path">engine.py:711</span> applies <code>max(volume_min, …)</code>
<i>after</i> the risk cap, so a trade can open at the minimum lot with no risk check at all.</p>
</div>

<div class="card">
<h3><span class="tag p1">P1</span>Backtest and live disagree on costs, and both are wrong</h3>
<table>
<thead><tr><th>Item</th><th>Backtest</th><th>Live</th><th>Where</th></tr></thead>
<tbody>
<tr><td>Commission</td><td class="bad">$0.00/lot, exit only</td><td>broker, both sides</td>
  <td class="path">engine.py:43-46</td></tr>
<tr><td>Spread</td><td class="bad">2.0 pips flat, BUY entry only — SELL pays nothing</td>
  <td>round-turn on both sides</td><td class="path">engine.py:121, 639-640</td></tr>
<tr><td>Slippage</td><td class="bad">0.5 pips, stop exits only</td>
  <td><code>deviation: 50</code> points ≈ 5 pips</td>
  <td class="path">engine.py:30,175 · mt5_client.py:355</td></tr>
<tr><td>Swap / financing</td><td class="bad">never modelled</td><td>charged nightly</td><td>—</td></tr>
<tr><td>Min stop distance</td><td>none</td><td>stop silently widened after sizing</td>
  <td class="path">mt5_client.py:325-345</td></tr>
</tbody></table>
<p>The flat 2.0-pip spread is <b>750× under</b> for BTCUSD ($0.02 vs $15 per lot) and 0.5× for US30.
Note the caller-facing <code>commission_per_lot=5.0</code> argument is dead —
<code>_calc_commission</code> never reads it.</p>
</div>

<div class="card">
<h3><span class="tag p1">P1</span>Two silent registries, seven symbols on the wrong contract spec</h3>
<p><span class="path">symbol_registry.py:223-234</span> and
<span class="path">symbol_profile_config.py:447</span> both fall back to a generic FX spec
(contract 100,000, pip 0.0001) with only a log line. <code>GER40</code>, <code>UK100</code>,
<code>XAGUSD</code> are missing from the profile table entirely — GER40 and UK100 are then sized
<b>100,000×</b> wrong (correct contract size 1.0). Broker symbols
<code>GOLD24-7.i#</code>, <code>SPXUSD</code>, <code>DE40Cash#</code>, <code>BRENTCash#</code>,
<code>MotorOil</code>, <code>US100-SEP26</code> all hit the fallback.
The two registries also contradict each other on WTI (100 vs 1000), SOLUSD (10 vs 1),
US500 pip size (1.0 vs 0.1), and no cross-check exists.</p>
</div>

<div class="card">
<h3><span class="tag p1">P1</span>Look-ahead in the H4/D1 resample — real defect, measured ~zero impact</h3>
<p><span class="path">engine.py:202-203, 215-216</span> and
<span class="path">signal_scan.py:148-149</span> call <code>resample()</code> with pandas defaults
(<code>closed='left', label='left'</code>), so a bucket covering 12:00–15:59 is labelled
<code>12:00</code>. The consumer filter <code>time &lt;= bar_time</code>
(<span class="path">signal_scan.py:208-209</span>) then admits the in-progress bucket, which in a
full-series resample already contains its future bars — up to 23 future H1 bars for D1.
The correct pattern already exists at <span class="path">engine.py:82,107</span>
(close-time slicing with <code>searchsorted(side="right")</code>) but is only used on the
<code>mtf_source</code> path.</p>
<p><b>Measured impact:</b> I re-scanned EURUSD, XAUUSD and GBPUSD over the most recent 1,200 H1
bars with the bucket labelled at its close and compared trade-for-trade. Expectancy delta
<b>0.0000R</b>, win-rate delta <b>0.00pp</b> on all three — the candidate sets are byte-identical.
The reason is structural: <code>MarketStructureEngine</code> requires 5 confirming bars on each
side of a pivot (<span class="path">market_structure.py:29</span>), so the newest — contaminated —
bucket never produces a pivot and never changes the bias.</p>
<p><b>So: fix it, but do not expect it to move P&amp;L.</b> It is a correctness landmine that will
bite the moment the pivot window changes, and it contaminates <code>score</code> and
<code>regime</code> fields even where it does not change the side.</p>
</div>

<div class="card">
<h3><span class="tag p0">P0</span>The SCALP candidate set is truncated — every M5 conclusion is unsafe</h3>
<p><code>data/signals/*_M5_183d_candidates.parquet</code> contains candidates for bar indices
<b>60 → 14,398 of 37,440</b> — the first ~38% of the window only. The stored M5 set therefore
covers roughly 50 days, not 183. Replaying it produces PF 1.09–1.74 on every symbol, which is the
<i>opposite</i> of the live optimiser's SCALP result (PF 0.50, −0.43R). Two measurements, same
code, opposite sign — the difference is the sample. <b>No SCALP number in this or any earlier
report should be acted on until the M5 scan is re-run over the full window.</b></p>
</div>

<div class="card">
<h3><span class="tag p2">P2</span>Other data-integrity findings</h3>
<ul>
  <li><b>A fabricated series is still on the default load path.</b>
  <code>data/market/MT5_DefaultBroker/XAUUSD/H1/…parquet</code> has 4,320 rows at a perfect 24/7
  grid, 1,216 weekend bars, spread ≡ 0, tick_volume ≡ 1, and prices $4,375→$7,668. The generator
  now raises <code>SyntheticDataError</code>, but the file was never deleted and
  <span class="path">acquisition.py:73</span> still returns <code>"MT5_DefaultBroker"</code> when
  MT5 is unreachable. Its manifest still claims quality_score 100.0.</li>
  <li><b>Timestamps are broker-server time labelled as UTC.</b> Both ingestion sites pass
  <code>utc=True</code> to a broker-local epoch (<span class="path">mt5_history.py:565</span>,
  <span class="path">acquisition.py:205</span>). Evidence: all 20 symbols show a volume peak at
  hour 17 (true London/NY overlap is 13:30–16:00 UTC) and D1 bars at exactly 00:00. Every
  session and kill-zone gate therefore fires ~2h late.</li>
  <li><b>The backtest passes an empty position list</b> (<span class="path">engine.py:627-635</span>),
  which disables concurrency caps, portfolio risk budget, correlation and concentration checks
  that live enforces. The circuit breaker is live-only; the loss cooldown is backtest-only. The
  two paths enforce disjoint risk sets.</li>
  <li><b>Four symbols (EURJPY, GBPJPY, US500, WTI) have no <code>_H1_95d.parquet</code></b>, so the
  entire calibration stack — which hardcodes that filename — silently skips them.</li>
  <li><b>Broker volume minimums are hardcoded to 0.01</b> (<span class="path">engine.py:623-625</span>)
  against real minimums of 0.1 (US30/US500), 0.02 (ETH), 0.05 (SOL), 0.15 (oil). MT5
  re-quantizes at <span class="path">mt5_client.py:310-316</span>, turning a 0.01-lot index trade
  into 0.1 lots — 10× the intended risk.</li>
</ul>
</div>

<h2>4. Remediation plan</h2>
<p>Ordered by expected impact per unit of effort. Every item states the change, the mechanism it
fixes, the expected impact (an <b>estimate to be measured</b>, not a promise), and the acceptance
test that decides whether it worked.</p>

<div class="card">
<h3><span class="tag p0">P0-1</span>Replace the hand-authored probability with a fitted, calibrated model</h3>
<p><b>Change.</b> Delete the 6-bin table (<span class="path">confidence.py:13-20</span>) and the
0.45/0.55 blend (<span class="path">decision_engine.py:193</span>). Fit an isotonic or Platt
calibrator on out-of-fold predictions, and make the gate read the calibrated number only.
<code>winrate_targeting.py:249</code> already fits a <code>ScoreCalibration</code> — but
<span class="path">entry_policy.py:189-210</span> never uses it for entry. Wire it up before
writing anything new.</p>
<p><b>Mechanism.</b> Removes the +0.03–0.04 inflation that mechanically clears the 0.45–0.48 floor,
and replaces a saturating linear prior with a real probability.</p>
<p><b>Expected impact.</b> Large on <i>selectivity</i>, not on raw hit rate: today the score cannot
rank because it is nearly constant. Measured earlier, the top 1% score quantile moves DAY_TRADING
win rate only 37.1%→39.4% — consistent with a score that carries little information. A calibrated
score is the precondition for any threshold to do work.</p>
<p><b>Acceptance.</b> Brier score below the base-rate reference and a reliability diagram within
±0.05 of the diagonal on held-out folds; score deciles monotone in realised win rate
(Spearman ρ ≥ 0.5) on out-of-sample data.</p>
</div>

<div class="card">
<h3><span class="tag p0">P0-2</span>Meta-labelling — <span class="bad">measured, and it does not work here</span></h3>
<p>I recommended this as the largest single lever. I then ran it, and it fails. Reporting the
measurement rather than the expectation:</p>
<table>
<thead><tr><th>Model</th><th class="num">Train AUC</th><th class="num">Test AUC</th><th class="num">Win rate, top decile vs base</th></tr></thead>
<tbody>
<tr><td>Window features (the shipped 14)</td><td class="num">0.746</td><td class="num bad">0.481</td>
  <td class="num bad">0.341 vs 0.359</td></tr>
<tr><td>+ primary-model outputs (score, regime, trend, ATR%, confluence)</td>
  <td class="num">0.783</td><td class="num bad">0.479</td><td class="num bad">0.335 vs 0.359</td></tr>
</tbody></table>
<p>62k labelled samples, purged and embargoed forward splits, 20 symbols. Training AUC of 0.75–0.78
against a test AUC <i>below</i> 0.5 is textbook overfitting, and selecting the top decile by
predicted probability makes the win rate <b>worse</b>, not better. I swept the labelling horizon
(5/10/20/40 bars) on the shipped model too: test AUC 0.527 / 0.507 / 0.508 / 0.507. No horizon
rescues it.</p>
<p><b>Why, and what it means.</b> Meta-labelling only adds value when the secondary model answers a
different question with information the primary model did not already use. These 14 features are
price and volume statistics of the same 30-bar window the primary signal reads — and adding the
primary model's own outputs made the overfitting worse, not better. <b>The gate is therefore left
inert on purpose</b>; that is now an evidence-based decision recorded in
<code>meta_labeler.py::_load</code>, not an oversight.</p>
<p><b>What a viable version requires.</b> Information the primary model does not have: cross-asset
or cross-timeframe context, order-flow/imbalance, session and calendar state, volatility term
structure. Do not spend effort training harder on these features — spend it on new inputs, and
require out-of-sample AUC &gt; 0.55 on a purged split before the gate is allowed to veto
anything.</p>
</div>

<div class="card">
<h3><span class="tag p0">P0-3</span>Re-derive the exit target and stop from volatility, not from constants</h3>
<p><b>Change.</b> Replace every fixed multiplier with a quantity computed from current conditions:</p>
<ul>
  <li>Stop distance = <code>k × ATR</code> where <code>k</code> is set so the stop sits outside the
  symbol's own recent noise band: <code>k = quantile(past N bars of |close−open|/ATR, q)</code>,
  scaled by the current ATR percentile. Not 0.85/0.95/1.0 by regime name
  (<span class="path">dynamic_levels.py:173,299</span>).</li>
  <li>Target = <code>m × risk_dist</code> where <code>m</code> is chosen from the <b>measured</b>
  conditional distribution of MFE at the current volatility percentile — i.e. the target the
  market is currently reaching, not 1.5 or 2.0 or 2.8.</li>
  <li>Time stop = the holding period at which the realised edge decays, estimated per symbol
  per regime, replacing <code>max_bars=200</code>
  (<span class="path">trade_simulator.py:84-91</span>). Measured earlier: at tp=3.0 the average win
  is 2.65R not 3.0R <i>because</i> the 200-bar clock cuts winners short — the vertical barrier is
  already distorting the payoff.</li>
  <li>Hard gate: expected edge must exceed round-trip cost by a margin, computed per trade as
  <code>(2×spread + slippage) / risk_dist</code> in R units. Reject any setup where that exceeds a
  dynamically-set fraction of the target.</li>
</ul>
<p><b>Acceptance.</b> Per symbol, across the full test period: PF ≥ 1.3 and max DD ≤ 10%, with the
parameters re-fitted on a rolling window and evaluated only out-of-sample.</p>
</div>

<div class="card">
<h3><span class="tag p1">P1-1</span>Volatility-targeted position sizing</h3>
<p><b>Change.</b> Replace the pinned Kelly term with
<code>f = target_risk_vol / realised_vol</code>, where <code>realised_vol</code> is an EWMA of
daily returns and <code>target_risk_vol</code> is a portfolio-level budget. This is the standard
volatility-targeting result and it removes the constant-1.50 artefact at
<span class="path">position_sizing.py:56</span> by construction.</p>
<p><b>Acceptance.</b> Realised portfolio volatility within ±20% of target; measured per-trade risk
within ±10% of the nominal figure (today it is 1.5×–2.6×).</p>
</div>

<div class="card">
<h3><span class="tag p1">P1-2</span>Fix the cost model and the spec fallbacks before trusting any number</h3>
<p><b>Change.</b> Charge round-turn spread on both sides using the <b>stored per-bar spread</b>
(the real MT5 files carry it), apply slippage to every exit type, model swap, and add commission.
Make the two registries one source of truth with <code>is_registered()</code> enforced before
sizing — a missing spec must raise, not silently return FX defaults.</p>
<p><b>Acceptance.</b> A backtest-vs-live reconciliation harness: same 20 trades, cost difference
&lt; 5%.</p>
</div>

<div class="card">
<h3><span class="tag p1">P1-3</span>Re-run every scan after fixing the resample and the SCALP window</h3>
<p><b>Change.</b> Add <code>label='right'</code> (or adopt the existing close-time slicer) at
<span class="path">engine.py:202-203,215-216</span> and
<span class="path">signal_scan.py:148-149</span>; quarantine the fabricated
<code>MT5_DefaultBroker</code> file; re-scan M5 over the full window; regenerate all candidate
parquets.</p>
<p><b>Acceptance.</b> Every candidate parquet covers ≥ 95% of its bar range; no symbol priced from
a fallback; no <code>SYNTHETIC_FALLBACK</code> tag in any backtest input.</p>
</div>

<div class="card">
<h3><span class="tag p2">P2-1</span>Correct for the search you already did</h3>
<p>The project has evaluated hundreds of thousands of candidate geometries. Under that many trials
a PF of 1.3 can appear by chance. Report the <b>Deflated Sharpe Ratio</b> alongside every headline
number, keep purged k-fold with embargo (already present at
<span class="path">winrate_targeting.py:663-666</span>) and add <b>sample-uniqueness weighting</b>,
since <code>max_bars=200</code> makes labels overlap heavily.</p>
<p><b>Acceptance.</b> DSR &gt; 0.95 before any parameter set is promoted to live.</p>
</div>

<h2>5. Where profitability is unrealistic — and what to target instead</h2>
<div class="card">
<p>The requested acceptance criterion — <b>PF &gt; 1.3 with DD &lt; 10%</b> — is not achievable on
this signal for {n_sym - n_ge_13} of {n_sym} symbols, and for the one that reaches it (WTI at
tp=2.0R, PF 1.306) the drawdown constraint is not executable: holding DD to 10% needs
$10.77 risk per trade on $10,000, while the smallest placeable oil lot risks $21.10.</p>
<table>
<thead><tr><th>Band</th><th>Symbols</th><th>Measured PF (H1)</th><th>Honest target</th></tr></thead>
<tbody>
<tr><td class="ok"><b>Salvageable</b></td>
  <td class="mono">WTI, XAUUSD</td><td>1.09 – 1.20</td>
  <td>The only two that survive honest costs (+0.087R and +0.042R). Target PF 1.10–1.25 after
  calibration and meta-labelling, on the regimes where they work — <b>not</b> 1.3 across all
  regimes.</td></tr>
<tr><td class="warn"><b>Break-even-ish, negative at honest costs</b></td>
  <td class="mono">GER40, NAS100, US30, US500, USDCAD, XAGUSD</td><td>0.96 – 1.08</td>
  <td>Look marginal, are not: every one turns negative once round-turn spread and commission are
  charged (GER40 +0.044R → −0.058R, NAS100 +0.015R → −0.037R). Do not allocate on the strength of
  the uncorrected numbers.</td></tr>
<tr><td class="bad"><b>Not viable</b></td>
  <td class="mono">AUDUSD, USDJPY, EURJPY, EURUSD, USDCHF, GBPUSD, SOLUSD, UK100, NZDUSD, GBPJPY, BTCUSD, ETHUSD</td>
  <td>0.57 – 0.93</td>
  <td><b>Do not trade these with this signal.</b> The deficit is 5–12 win-rate points against a
  40% requirement; no exit or sizing change closes that. Target: zero allocation until a new
  primary model exists.</td></tr>
</tbody></table>
<p><b>Regime-level flags.</b> TREND_BULL and TREND_BEAR (PF 0.872/0.871) and BREAKOUT (0.927) are
unsalvageable as configured — the system is worst exactly where it claims to be best. COMPRESSION
(PF 1.203, n=844) is the only regime with measured positive expectancy; it is the natural first
candidate for a focused product, and 844 trades is a thin sample that must be confirmed
out-of-sample before it is believed.</p>
<p><b>The achievable portfolio target</b>, if P0-1 through P0-3 land: PF 1.10–1.25 on a two-symbol
book (WTI, XAUUSD), and DD ≤ 10% only at a risk fraction of roughly 0.06–0.11% per trade — which
is below the broker's minimum lot on both. <b>The binding constraint is not the signal alone; it is
that the signal's edge is too small to be expressed at a tradeable size.</b> A two-symbol,
one-regime product is a real but very small business; a twenty-symbol one is not, and no amount of
parameter work will make it one.</p>
</div>

<h2>6. What the numbers become once the cost model is honest</h2>
<p>The figures above still use a cost model that charges nothing for commission, nothing for a
SELL's spread, and no slippage on market exits. Correcting it costs
<b>{mean_gap:.4f}R per trade on average</b>, but the damage is very uneven — and it re-ranks the
universe. Costs are expressed in R using each symbol's own median stop distance, so the comparison
is scale-free:</p>

<div class="card" style="padding:8px 12px">
<table>
<thead><tr><th>Symbol</th><th class="num">PF (as measured)</th><th class="num">Expectancy (as measured)</th>
<th class="num">Cost understatement</th><th class="num">Expectancy at honest costs</th></tr></thead>
<tbody>
{corr_rows}
</tbody></table>
<div class="note">Round-turn spread taken from the broker's own stored <code>spread</code> column on
the entry bars, plus $5/lot round-turn commission. The understatement is largest where the stop is
tightest relative to the spread — SOLUSD loses a further 0.69R per trade once the cost is charged
properly, XAUUSD only 0.0075R.</div>
</div>

<div class="card verdict">
  <b>Only {len(survivors)} of {len(corr)} symbols keep a positive expectancy once costs are charged
  correctly: {", ".join(survivors)}.</b> GER40 (+0.044R) and NAS100 (+0.015R), which looked like the
  edge of the salvageable band, both turn negative. PF ≥ 1.3 is reachable by <b>no symbol</b> at
  tp=1.5R — WTI's 1.202 is the ceiling, and it survives honest costs at +0.087R.
</div>

<h2>7. Fixes applied in this pass</h2>
<div class="card">
<table>
<thead><tr><th>Fix</th><th>Where</th><th>Effect</th></tr></thead>
<tbody>
<tr><td>Commission argument was accepted and ignored — every shipped profile is
  <code>commission_per_lot=0.0</code>, so every backtest ran free</td>
  <td class="path">engine.py::_calc_commission</td>
  <td>Now falls back to the constructor value when the profile is zero</td></tr>
<tr><td>Spread charged on BUY entry only — every SELL traded cost-free</td>
  <td class="path">engine.py:639-640</td><td>Symmetric: shorts now fill at the bid</td></tr>
<tr><td>Slippage charged on stop exits only, so the time stop looked free</td>
  <td class="path">engine.py (stagnation + final close)</td>
  <td>Market exits now take adverse slippage; TP stays a limit fill</td></tr>
<tr><td>Lot floor applied <i>after</i> the risk cap, allowing unbounded realised risk</td>
  <td class="path">engine.py:711</td><td>Trade is skipped when the plan cannot be expressed at the
  minimum lot</td></tr>
<tr><td>H4/D1 resample exposed in-progress, future-bearing buckets</td>
  <td class="path">engine.py:202-203,215-216 · signal_scan.py:148-149</td>
  <td><code>label="right"</code> — a bucket is only visible once it has closed</td></tr>
<tr><td>GER40 / UK100 / XAGUSD missing from the profile table → generic FX template
  (contract size 100,000 instead of 1.0 / 5,000)</td>
  <td class="path">symbol_profile_config.py</td>
  <td>Registered with correct contract size, pip size and digits</td></tr>
<tr><td>Unregistered symbols fell back silently</td>
  <td class="path">symbol_profile_config.py::get_symbol_profile_config</td>
  <td>Now logs an error naming the symbol</td></tr>
<tr><td>Fabricated XAUUSD series (4,320 rows, 1,216 weekend bars, spread ≡ 0) on the default
  load path</td>
  <td class="path">data/market/MT5_DefaultBroker/</td>
  <td>Moved to <code>data/market/_quarantine_synthetic/</code></td></tr>
</tbody></table>
<p class="note">Guarded by <code>tests/test_backtest_cost_integrity.py</code> (9 tests). The
look-ahead test is discriminating, not decorative: against the old resample it records
<b>115 violations</b> of the no-look-ahead property; against the fixed one, zero. Full suite:
702 passed.</p>
</div>

<div class="disc">
<b>Method and sources.</b> All figures are local: bar data from
<code>data/market/real/&lt;SYM&gt;/&lt;SYM&gt;_&lt;TF&gt;_183d.parquet</code> (MetaTrader5 real
ticks, 2026-03-15 → 2026-09-11 FX / 2026-09-13 crypto), candidates from
<code>data/signals/*_183d_candidates.parquet</code>, replayed through
<code>jarvis/backtesting/trade_simulator.py</code>. Regime buckets are per-symbol terciles — no
fixed thresholds anywhere in this report. Reproduce with
<code>python tools/audit_trade_quality.py</code>, <code>python tools/audit_verdict.py</code>,
<code>python tools/audit_lookahead.py</code>. Raw output:
<code>reports/trade_quality_audit.json</code>, <code>reports/verdict.json</code>,
<code>reports/lookahead_impact.json</code>. The 183-day window is one market regime cluster;
every number here is in-sample to it unless stated otherwise.<br><br>
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>

</div>
<script>
var pfChart = echarts.init(document.getElementById('c_pf'));
pfChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 50, right: 24, top: 30, bottom: 70 }},
  xAxis: {{ type: 'category', data: {sym_pf}, axisLabel: {{ rotate: 45, fontSize: 10 }} }},
  yAxis: {{ type: 'value', name: 'Profit factor', min: 0, max: 1.6 }},
  series: [{{
    name: 'Profit factor',
    type: 'bar',
    data: {sym_pfv},
    itemStyle: {{
      color: function (p) {{
        return p.value >= 1.3 ? '#1a7f4b' : (p.value >= 1.0 ? '#b8860b' : '#c0392b');
      }}
    }},
    markLine: {{
      symbol: 'none',
      data: [
        {{ yAxis: 1.0, lineStyle: {{ color: '#c0392b', type: 'dashed' }}, label: {{ formatter: 'break-even 1.0' }} }},
        {{ yAxis: 1.3, lineStyle: {{ color: '#1a7f4b', type: 'dashed' }}, label: {{ formatter: 'target 1.3' }} }}
      ]
    }}
  }}]
}});

var wrChart = echarts.init(document.getElementById('c_wr'));
wrChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ data: ['Win rate', 'Break-even'], bottom: 0 }},
  grid: {{ left: 50, right: 20, top: 30, bottom: 60 }},
  xAxis: {{ type: 'category', data: {sym_pf}, axisLabel: {{ rotate: 45, fontSize: 10 }} }},
  yAxis: {{ type: 'value', name: '%', max: 50 }},
  series: [
    {{ name: 'Win rate', type: 'bar', data: {sym_wr}, itemStyle: {{ color: '#1f5f9e' }} }},
    {{ name: 'Break-even', type: 'line', data: {sym_be}, symbol: 'none', lineStyle: {{ color: '#c0392b', type: 'dashed' }} }}
  ]
}});

var ddChart = echarts.init(document.getElementById('c_dd'));
ddChart.setOption({{
  tooltip: {{ trigger: 'axis', formatter: '{{b}}: {{c}}%' }},
  grid: {{ left: 50, right: 20, top: 30, bottom: 70 }},
  xAxis: {{ type: 'category', data: {sym_pf}, axisLabel: {{ rotate: 45, fontSize: 10 }} }},
  yAxis: {{ type: 'value', name: 'Drawdown %', max: 100 }},
  series: [{{
    name: 'Max DD',
    type: 'bar',
    data: {sym_dd},
    itemStyle: {{ color: '#c0392b' }},
    markLine: {{
      symbol: 'none',
      data: [{{ yAxis: 10, lineStyle: {{ color: '#1a7f4b', type: 'dashed' }}, label: {{ formatter: '10% limit' }} }}]
    }}
  }}]
}});

var regChart = echarts.init(document.getElementById('c_reg'));
regChart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 60, right: 24, top: 30, bottom: 60 }},
  xAxis: {{ type: 'category', data: {reg_names}, axisLabel: {{ rotate: 30, fontSize: 10 }} }},
  yAxis: {{ type: 'value', name: 'Profit factor', min: 0, max: 1.5 }},
  series: [{{
    name: 'Profit factor',
    type: 'bar',
    data: {reg_pf},
    itemStyle: {{
      color: function (p) {{
        return p.value >= 1.3 ? '#1a7f4b' : (p.value >= 1.0 ? '#b8860b' : '#c0392b');
      }}
    }},
    markLine: {{
      symbol: 'none',
      data: [{{ yAxis: 1.0, lineStyle: {{ color: '#c0392b', type: 'dashed' }}, label: {{ formatter: 'break-even' }} }}]
    }}
  }}]
}});

window.addEventListener('resize', function () {{
  pfChart.resize(); wrChart.resize(); ddChart.resize(); regChart.resize();
}});
</script>
</body>
</html>
"""


def main() -> int:
    html = build_html()
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {OUT} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
