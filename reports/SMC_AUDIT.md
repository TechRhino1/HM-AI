# Smart Money Concepts (SMC) — Implementation Audit

**Date:** 2026-09-12
**Scope:** liquidity pools (BSL/SSL) + sweep detection, Fair Value Gaps, Order Blocks.
Verification: where implemented, whether detection rules / lookbacks / invalidation /
confirmation are consistent, and whether they actually reach entry, SL and TP.

---

## 1. Where each concept lives

| Concept | Primary implementation | Consumed by |
|---|---|---|
| Liquidity pools (BSL/SSL), sweep | `jarvis/market/liquidity.py` (L60–135) | `dynamic_levels.py` (SL/TP anchors), `liquidity_analyst.py` |
| BOS / CHoCH / swing structure | `jarvis/market/market_structure.py` (L44–62) | `StructureContext` → levels, analysts |
| Order Blocks (variant A) | `jarvis/market/market_structure.py` (L82–107) | **`dynamic_levels.py` → SL/TP** |
| Fair Value Gaps (variant A) | `jarvis/market/market_structure.py` (L109–121) | **`dynamic_levels.py` → SL/TP** |
| Order Blocks (variant B) | `jarvis/market/fair_value_gap.py` (L97–114) | `decision_engine.py:858`, `master_confluence.py:138` → **scoring only** |
| Fair Value Gaps (variant B) | `jarvis/market/fair_value_gap.py` (L58–86) | same → **scoring only** |
| OTE (62 / 70.5 / 79) | `jarvis/market/fair_value_gap.py` (L161–197) | `decision_engine.py:863` |
| Premium / Discount / Equilibrium | `market_structure.py` (L64–76) | `StructureContext` |

---

## 2. What is CORRECT

1. **Liquidity pool definition is standard** — `liquidity.py:133-134`:
   `buy_side_liquidity = recent_sh`, `sell_side_liquidity = recent_sl`.
   Buy-side liquidity rests above swing highs; sell-side below swing lows. Correct.

2. **Sweep detection is well specified** — `liquidity.py:105-118`. A bullish sweep
   requires: candle low **breaks** the swing low, **closes back above** it, closes
   bullish, displacement (body ≥ 45% of range), volume ≥ 40% of the 14-bar average,
   and penetration of **0.15–3.5 ATR** (rejects both noise and runaway bars). This is
   textbook SMC-grab-plus-rejection and the strongest part of the implementation.

3. **FVG detection is correct and consistent across both engines** —
   `market_structure.py:113` `lows[i] > highs[i-2]` and `fair_value_gap.py:65`
   `c1_high < c3_low` are the same 3-candle imbalance test. Good.

4. **BOS / CHoCH are standard** — `market_structure.py:49-62` (CHoCH requires the
   prior leg to be LH/LL or HH/HL before the break).

5. **SMC genuinely reaches SL/TP**, not just narrative —
   `dynamic_levels.py:140-149` (BUY: demand zone, bullish OB low, bullish FVG bottom,
   SSL, key levels → `min()` anchor, capped at 3 ATR), `:207-218` (TP: bearish FVG,
   BSL, liquidity pools), `:256-265` and `:327` (SELL mirror).

---

## 3. Defects

### B1 (HIGH) — Two divergent OB/FVG implementations; the mitigation-aware one never reaches SL/TP

`market_structure.py:82-121` and `fair_value_gap.py:58-114` both detect order blocks
and FVGs, with **different rules and different lookbacks**:

| | market_structure (feeds SL/TP) | fair_value_gap (feeds scoring) |
|---|---|---|
| OB rule | opposite candle + close beyond it + displacement ≥ 45% of next range | body > 1.5 × ATR + opposite prior candle |
| OB lookback | 15 bars | 30 bars |
| FVG lookback | 20 bars | 50 bars |
| Mitigation tracked? | **No** | Yes |

**Impact:** `StructureContext.order_blocks` / `.fair_value_gaps` carry **no
`mitigated` flag at all**, yet `dynamic_levels.py` anchors stop-loss and take-profit
directly to them. A stop can therefore be placed on an FVG or order block that price
has already traded through and consumed. Meanwhile the engine that *does* track
mitigation is only used to add score.

**Minimal correction (pick one):**
- *Preferred:* add a `mitigated` flag to the `market_structure.py` FVG/OB dicts and
  filter in `dynamic_levels.py` (`if not fvg.get("mitigated")`) — ~6 lines, no
  behaviour change beyond excluding consumed zones.
- *Or:* have `dynamic_levels.py` read the `FairValueGapEngine` result instead.
- Longer term: delete one implementation. Two definitions of "order block" will keep
  drifting.

### B2 (HIGH) — Stop-loss anchored *at* liquidity rather than beyond the sweep

`dynamic_levels.py:148-149` (BUY) and `:264-265` (SELL):
```python
if 0 < context.liquidity.sell_side_liquidity < entry_price:
    candidate_anchors.append(float(context.liquidity.sell_side_liquidity))
```
SSL/BSL are then eligible to be chosen as the stop anchor (`anchor = min(...)`).

Liquidity pools exist precisely so they *can* be swept — that is the entry trigger the
system trades. A stop resting at (or a nominal buffer below) the pool is taken out by
the very displacement the strategy is designed to exploit. The comment calls this an
"Anti-Wick Shield", but anchoring to liquidity does the opposite.

**Minimal correction:** when `context.liquidity.sweep_detected` is true, anchor the
stop beyond the sweep extreme rather than at the pre-sweep level —
e.g. use `sweep_level - max(sweep_magnitude, 0.5) * atr` for a long. Otherwise require
structural anchors (OB low / FVG bottom / swing) strictly *beyond* the pool and drop
raw SSL/BSL from `candidate_anchors`. Roughly 4 lines at each of the two sites.

### B3 (MEDIUM) — Mitigation is a wick touch, not a trade-through

`fair_value_gap.py:68 / 80 / 107 / 113`:
```python
mitigated = bool(np.any(lows[i+1:] <= top))     # bullish FVG / OB
mitigated = bool(np.any(highs[i+1:] >= bottom)) # bearish FVG / OB
```
A single tick into the zone marks it consumed. In SMC an FVG is mitigated when price
*trades through* it, and an order block is invalidated by a close beyond its far side.
This makes valid zones disappear prematurely.

**Minimal correction:** for a bullish FVG require `lows[i+1:] <= bottom` (full fill);
for a bullish OB require `closes[i+1:] < bottom` (close through). Mirror for bearish.
Same line count.

### B4 (MEDIUM) — Order block lacks break-of-structure confirmation

`fair_value_gap.py:103-108` accepts an OB on `body_size > 1.5 × ATR` plus an opposite
prior candle. It never requires the displacement to break a swing high/low. Many
"order blocks" are therefore just large candles with no structural consequence.

**Minimal correction:** additionally require the displacement candle to close beyond the
most recent swing high (bullish) / swing low (bearish) — the BOS test already exists in
`market_structure.py:49-50` and can be reused.

### B5 (MEDIUM) — FVG/OB confluence ignores direction

`fair_value_gap.py:155-159` iterates every active FVG against every active OB with **no
direction match**, so a *bullish* FVG overlapping a *bearish* OB flags confluence. Those
are contradictory signals.

**Minimal correction:** only compare same-direction pairs, e.g.
`if fvg_dir == ob_dir and fvg['bottom'] <= ob['top'] and fvg['top'] >= ob['bottom']`.

### B6 (MEDIUM) — Fallback fabricates liquidity from ATR

`dynamic_levels.py:526-527` (fallback `LiquidityContext`):
```python
buy_side_liquidity=round(price + (atr * 2.0), digits),
sell_side_liquidity=round(price - (atr * 2.0), digits)
```
That is an ATR band, not detected liquidity. If this path is ever taken, the
"liquidity-anchored" levels are synthetic and the name is misleading.

**Minimal correction:** set both to `0.0` in the fallback so the existing
`if 0 < ...` guards skip them and the no-anchor branch (ATR-based SL) is used honestly.

### B7 (LOW) — "Unmitigated" asserted without a mitigation flag

`liquidity_analyst.py:38-40` reports
`f"Unmitigated {recent_fvg['type']} provides institutional magnet"` using
`st.fair_value_gaps[-1]` — but that structure (variant A) tracks no mitigation, so the
word "Unmitigated" is unverified.

**Minimal correction:** drop the word, or source the FVG from `FairValueGapEngine`
(which does track mitigation).

### B8 (LOW) — Float equality in swing-pivot detection

`market_structure.py:30,32` use `highs[i] == max(...)` / `lows[i] == min(...)`. Exact
float comparison; ties match multiple bars or none.

**Minimal correction:** use `>=` (or `argmax`) to make pivot selection deterministic.

### B9 (LOW) — Inconsistent lookback windows

FVG 20 bars (`market_structure`) vs 50 (`fair_value_gap`); OB 15 vs 30; OTE 60.
Not wrong per se, but undocumented and a source of the divergence in B1.

**Minimal correction:** hoist to named constants with a short comment on why each window.

### B10 (LOW) — Zone semantics

- `market_structure.py:79-80`: `demand_zone = (recent_sl, recent_sl * 1.003)` — a fixed
  0.3% band placed *above* the swing low, and `supply_zone` mirrored with 0.997. A
  percentage constant is not volatility-scaled and the direction of the band is debatable.
  **Suggestion:** ATR-relative width, e.g. `recent_sl ± 0.25 × atr`.
- `fair_value_gap.py:72,84`: FVG `bar_idx = i - 2`. The gap conventionally belongs to
  the middle candle (`i - 1`). Positional vs dataframe index should also be documented.

---

## 4. Summary verdict

| Concept | Present | Rules sound | Feeds entry | Feeds SL/TP | Invalidation |
|---|:--:|:--:|:--:|:--:|:--:|
| Liquidity pools (BSL/SSL) | Yes | Yes | Yes | Yes (but see B2) | Status tracked, unused (B2/B6) |
| Sweep detection | Yes | **Yes — best implemented** | Yes | — | n/a |
| Fair Value Gap | Yes (×2) | Yes | Scoring only (B1) | Yes, **without mitigation (B1/B3)** | B3 |
| Order Block | Yes (×2) | Partial (B4) | Scoring only (B1) | Yes, **without mitigation (B1)** | B3 |
| OTE / Premium-Discount | Yes | Yes | Scoring | No | n/a |

**The concepts are all present and the sweep logic is genuinely good.** The two defects
that actually change trade outcomes are **B1** (SL/TP anchoring to zones with no
mitigation tracking) and **B2** (stops placed at liquidity that is meant to be swept).
Both are a handful of lines each — no rewrite required.
