# Markets data contracts — stocks and India endpoints

Reference for anyone binding a panel to the stocks / India REST surface. Every
shape below was read from the service code **and** observed on the wire; nothing
here is inferred from the front-end alone.

Captured against a freshly restarted server on `127.0.0.1:8501` (2026-09-15).

## Why this document exists

Task: *"Determine the exact response shape of every stocks and India endpoint the
new Markets view will bind to … read the service code and the existing
`stocks.js` / `india.js` consumers rather than guessing, and record which
endpoints respond promptly versus hang on an external provider, since that
determines their timeout budget and empty state."*

The "hang" half of that turned out to matter a great deal — see
[§4](#4-the-three-that-hung-a-restart-fixed-them).

---

## 1. Timeout budget

The dashboard's own ladder, from `dashboard.js:46`. Bind every call to one of
these rather than inventing a number:

| Token | ms | Use for |
|---|---|---|
| `fast` | 8 000 | local, in-memory reads |
| `normal` | 15 000 | cheap computed payloads |
| `provider` | 30 000 | anything that reaches a live external feed |
| `slow` | 60 000 | full-universe scans |

**Bind the provider-backed routes to `provider` (30 s), not `normal` (15 s).**

Measured on a freshly started server:

| Route | Typical | Periodic bump |
|---|---|---|
| `/api/india/heatmap` | **0.01–1.35 s** | ~0.9 s every 60 s |
| `/api/india/indices` | 0.9 s | — |
| `/api/india/scanner` | 0.2–3.2 s | shares the master scan |
| `/api/stocks/screener` | 1.8–3.2 s | — |

These are comfortably fast. The reason for the 30 s budget is not the typical
case but the outlier below.

### Where the time actually goes

The whole India scan is **free except for one call**. Instrumenting it shows the
42-symbol universe is hydrated in a single batched `fetch_quotes()`, and that
call is the entire cost:

```
call 1:  3.53s   fetch_quotes: 1 call,  3.39s    <- the batched hydration
call 2:  0.14s   fetch_quotes: 0 calls, 0.00s    <- everything cached
call 3:  0.14s   fetch_quotes: 0 calls, 0.00s
```

So the latency is that one provider round-trip, paid once per **60 s** (the
hydrator's cache TTL). Measured on its own, with the provider cache cleared
before each sample, it costs **0.32–3.56 s** — the first call paying TLS and DNS
setup, the rest about 0.4 s.

`_scan_cache_ttl` is 15 s (`india_service.py:40`) and the rescan is far cheaper
than that, so the scan cache is not the constraint; the hydrator's 60 s TTL is
what sets the rhythm.

### One outlier, left open

The long-running server was observed at **up to 17.8 s** for heatmap and 17.1 s
for indices, on three consecutive samples. That figure is real but it does not
reproduce, and the honest position is that it is unexplained:

* not an endpoint cost — a fresh server answers in 0.01–1.35 s
* not the rescan — the rescan is 0.14 s, and the provider call is 0.32–3.56 s
* not thread contention — a `py-spy` dump while idle shows all four threads idle
* not startup warm-up — a fresh server polled from boot shows no slow stretch

It was only ever seen in the instance that had been up for a while, and that
instance later settled back to 2.6–2.9 s. Treat 17.8 s as an observed upper
bound and size the budget to absorb it; do not treat it as a fixed cost, and do
not assume it is gone.

**The measurement trap this section exists to record:** a spot-check taken right
after another call measures the *cache*, not the endpoint. The first figures
written here (0.31–3.44 s) were all inside the TTL window. Sample with gaps
longer than the TTL, or the number is fiction.

---

## 2. Stocks endpoints

### `GET /api/stocks/screener`

`?sort_by=probability&sort_dir=desc&limit=40` — the only form the UI uses.

Cold: **3.20 s** · Consumer: `dashboard.js:2511` (`TIMEOUT.provider`), `stocks.js:153`

```jsonc
{
  "stocks": [ /* 40 rows with limit=40 */ ],
  "ai_recommended_buys": [ /* 4 rows */ ],
  "count": 47,            // matching total, NOT len(stocks)
  "total_universe": 47,
  "filters": { /* echo of the query */ },
  "timeframe": "1D",
  "timestamp": "str"
}
```

**`count` is the matching total, not the page length.** Observed `count=47`
alongside `len(stocks)=40` under `limit=40`. A pager must use `count`, or it
will show a single page as if it were the whole result.

Rows carry their own provenance. `verify_ui_live.py` asserts **no unlabelled
rows and no bad fallbacks** on this endpoint (`fallback_count=0`).

### `GET /api/stocks/heatmap`

Cold: **0.05 s** · Consumer: `dashboard.js:2625` (`TIMEOUT.provider`), `stocks.js:777`

```jsonc
{ "sectors": [ /* object */ ], "count": 12 }
```

### `GET /api/stocks/news`

`?symbol=NVDA` — **requires `symbol`**; a bare request is not meaningful.

Cold: **0.01 s** · Consumer: **none**

```jsonc
{ "news": [ /* object */ ], "symbol": "NVDA" }
```

### `GET /api/stocks/recommended_buys`

Cold: **0.01 s** · Consumer: **none**

```jsonc
{ "recommended_buys": [ /* object */ ], "count": 4 }
```

> **Note — two endpoints have no UI consumer.**
> `/api/stocks/news` and `/api/stocks/recommended_buys` are reachable and
> exercised by `test_stocks_screener_api.py`, but no template or script calls
> them. The screener's `ai_recommended_buys` array is what the "AI Buy Now"
> grid actually renders (`stocks.js:159`, `india.js:155`). Either bind them or
> drop them; today they are maintenance surface with no reader.

---

## 3. India endpoints

### `GET /api/india/indices`

Typical **0.9 s** · Consumer: `dashboard.js:2694` (`TIMEOUT.provider`), `india.js:114`

The service returns a bare list; the route wraps it (`india_service.py:434`):

```jsonc
{ "indices": [ /* row */ ] }
```

Each row — 7 rows for the seven benchmark/sectoral indices:

```jsonc
{
  "symbol": "NIFTY",
  "name": "str",
  "price": 24175.65,
  "change_pct": 0.0, "change_val": 0.0,
  "cpr_classification": "AVERAGE_CPR", "cpr_label": "str",
  "camarilla_h4": 0.0, "camarilla_l4": 0.0,
  "vwap": 0.0,
  "bias": "BULLISH",
  "data_source": "live"          // "live" | "calibrated_feed" | "profile_reference"
}
```

**`data_source` is load-bearing.** `verify_ui_live.py` asserts every row names
its source. When the engine cannot produce a row, the service emits a
`profile_reference` row built from the static universe table
(`india_service.py:88-101`) — the price is a static reference and the Camarilla
bands are arithmetic on it. That row must never be presented as a market read.

### `GET /api/india/fii_dii`

Cold: **0.03 s** · Consumer: `dashboard.js:2770` (`TIMEOUT.normal`), `india.js:126`

```jsonc
{
  "date": "str",
  "fii_cash_net_cr": 0.0, "dii_cash_net_cr": 0.0,
  "total_net_institutional_cr": 0.0,
  "fii_index_futures_long_pct": 0.0, "fii_index_options_pcr": 0.0,
  "fii_sentiment": "str", "dii_sentiment": "str",
  "institutional_bias": "str"
}
```

`data_source='sample'` on the wire — these are **not** live NSE flows and the
payload says so. It is the only endpoint in this set that uses `TIMEOUT.normal`,
because nothing about it is a provider round-trip.

### `GET /api/india/option_chain?symbol=NIFTY`

Cold: **1.30 s** · Consumer: `dashboard.js:2840` (`TIMEOUT.provider`), `india.js:619`

```jsonc
{
  "symbol": "NIFTY", "name": "str",
  "spot_price": 0.0, "atm_strike": 24200.0,
  "strike_step": 0.0, "lot_size": 0,
  "expiry": "str", "expiry_schedule": { /* object */ },
  "freeze_limit": 0,
  "chain": [ /* object */ ],           // 25 strikes in the observed window
  "pcr": { /* object */ },
  "gex": { /* object */ },
  "iv_rank": 0.0,
  "atm_straddle": { /* object */ },
  "max_pain_strike": 0.0,
  "data_source": "synthetic"
}
```

`data_source='synthetic'` — the chain is modelled, and `verify_ui_live.py`
asserts the payload declares itself so. `atm_strike` is what the panel centres
its window on.

### `GET /api/india/options/recommendations`

Cold: **2.07 s** · Consumer: `india_options.js:235`

```jsonc
{ "recommendations": [ /* object */ ] }
```

### `GET /api/india/heatmap`

Typical **0.01–1.35 s**, with a ~0.9 s bump every 60 s · Consumer: `india.js:789`

```jsonc
{
  "sectors": [{
    "sector": "str",
    "count": 0, "stock_count": 0,
    "avg_change_pct": 0.0, "avg_probability": 0.0,
    "rotation_status": "str",
    "top_leader_symbol": "str", "top_leader_change": 0.0,
    "top_breakout_symbol": "str", "top_breakout_prob": 0,
    "data_source": "str"
  }]
}
```

11 sectors observed. `verify_ui_live.py` asserts this endpoint **reports only
what it computes** — no invented `avg_cmf`, and no unflagged row.

### `GET /api/india/scanner`

`?sector=&market=&cpr=&min_prob=&sort_by=&sort_dir=` — all optional.

Warm **0.2–3.2 s**, shares the master scan · Consumer: `india.js:150`

```jsonc
{
  "stocks": [ /* 35 rows */ ],
  "ai_recommended_buys": [ /* 4 rows */ ],
  "count": 35,
  "scanned_at": "2026-09-15T20:19:51.920557+00:00"
}
```

A `stocks` row is the richest payload in this set:

```jsonc
{
  "symbol": "str", "name": "str", "sector": "str",
  "price": 0.0, "change_pct": 0.0, "change_val": 0.0,
  "vwap": 0.0, "vwap_dist_pct": 0.0, "rvol": 0.0,
  "cpr": { "pivot": 0, "tc": 0, "bc": 0,
           "width_points": 0, "width_pct": 0,
           "width_classification": "str", "width_label": "str" },
  "camarilla": { "h1": 0, "h2": 0, "h3_reversal": 0, "h4_breakout": 0,
                 "l1": 0, "l2": 0, "l3_reversal": 0, "l4_breakdown": 0 },
  "score_breakdown": { "cpr_structure": 0, "camarilla_breakout": 0,
                       "vwap_corridor": 0, "vsa_volume": 0,
                       "momentum_squeeze": 0, "market_regime": 0 },
  "sebi_regulatory": { "asm_stage": 0, "gsm_stage": 0, "circuit_limit": 0,
                       "is_fno_ban": false, "mwpl_utilization_pct": 0 },
  "breakout_probability": 0, "opportunity_state": "str",
  "setup_grade": "str", "grade_badge": "str",
  "recommendation": "str",
  "entry_zone": 0.0, "stop_loss": 0.0, "take_profit_2": 0.0,
  "expected_gain_pct": 0.0, "max_risk_pct": 0.0,
  "lot_size": 0, "notional_contract_value_inr": 0.0,
  "is_index": false, "is_squeeze": false,
  "squeeze_status": "str", "market": "str"
}
```

`include_indices=False` (the default) is what keeps indices out of `stocks`.
`test_india_markets_api.py` asserts that separation, and that no index symbol
appears in `ai_recommended_buys` either. An `ai_recommended_buys` row is the
same shape minus the scan-only fields.

---

## 4. The three that hung: a restart fixed them

`/api/india/indices`, `/api/india/heatmap` and `/api/india/scanner` previously
**never returned** — measured past 240 s, while the server stayed healthy on
every other route. They are exactly the endpoints the Markets view binds to, so
this was a live user-facing defect, not only a documentation gap.

### What it was

`py-spy dump` on the running server showed a single request thread **997 frames
deep** in a repeating four-frame cycle:

```
fetch_quotes      (tradingview_provider.py)   <- the fallback branch
get_india_profile (jarvis/india/universe.py:760)
get_profile       (dynamic_hydrator.py:181)
hydrate_batch     (dynamic_hydrator.py:234)
fetch_quotes      (tradingview_provider.py)   <- ...and round again
```

`fetch_quotes`'s last-resort branch asked for a baseline price by calling
`get_india_profile()`, which hydrates, which resolves quotes by calling
`fetch_quotes()` — for the same symbol. Nothing raised, so the `except` clauses
never fired; the stack simply grew while each level opened another blocking
connection. Every index symbol reached that branch, which is why exactly the
index-bearing routes hung and `/api/india/fii_dii` did not.

The decisive evidence that this was **stale code, not a live bug**: the dump
showed the process executing `tradingview_provider.py:565` *calling
`get_india_profile`*, but in the working tree line 565 is a **comment**.

### What fixed it

Commit **`2c655c6`** — *"fix(data): break the quote-fallback recursion into the
profile hydrator"* — already in the tree. That branch now reads its baseline
straight from the static `INDIA_UNIVERSE` / `STOCK_UNIVERSE` tables, which is
all it ever needed. The server process, however, had been running since **before**
that commit, so it was still executing the recursive version.

**The only action required was a restart.** No code change was needed. Verified
after restarting:

| Endpoint | Before | After |
|---|---|---|
| `/api/india/indices` | hang > 240 s | **200, 3.44 s** |
| `/api/india/heatmap` | hang > 240 s | **200, 0.68 s** |
| `/api/india/scanner` | hang > 240 s | **200, 0.31 s** |

`tools/verify_ui_live.py`: **44/44**, now against a cold server. That detail
matters — the India checks (`indices rows=7 unflagged=[]`,
`heatmap sectors=11 invented_cmf=[]`) had been passing on a **warm cache**; they
now pass on a fresh process.

### The lesson worth keeping

Two habits made this findable, and both generalise:

1. **`py-spy dump --pid <pid>`** names the frame of a wedged *live* process
   without restarting it — which matters, because restarting destroys the very
   state you are diagnosing.
2. **A hang that is not reproducible in a fresh process is a stale-process
   problem, not a logic problem.** Compare the two before reading any code.

A bare `except Exception` around a hydrating call is what let a
`RecursionError` become a silent ~1000-second stall. That is the pattern to
watch for elsewhere.

---

## 5. Provenance summary

Every endpoint in this set declares the trustworthiness of its own payload. A
consumer must render the label, not assume the number is measured:

| Endpoint | Declared source | Meaning |
|---|---|---|
| `/api/india/indices` | `live` / `calibrated_feed` / `profile_reference` | per row |
| `/api/india/fii_dii` | `sample` | **not** live NSE flows |
| `/api/india/option_chain` | `synthetic` | modelled chain |
| `/api/india/heatmap` | per sector | computed from the master scan |
| `/api/india/scanner` | per row | same ladder as indices |
| `/api/stocks/screener` | per row | 40 rows, `fallback_count=0` observed |

The rule the project already works by: **a response returning modelled values
must say so in its own payload**, and a field the upstream provider did not send
reads `—` rather than a plausible-looking default.
