"""Check actual spreads from XM MT5 account for all tradeable symbols."""
import MetaTrader5 as mt5
import json

mt5.initialize()
acc = mt5.account_info()
print(f"Broker: {acc.server} | Account: #{acc.login} | Leverage: 1:{acc.leverage}")
print(f"Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")
print()

symbols = [
    "XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
    "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY",
    "US30", "US500", "ETHUSD", "SOLUSD", "OIL"
]

gold_aliases = ["GOLD.i#", "GOLD#", "GOLD", "XAUUSD", "XAUUSD#", "XAUUSDm", "XAUUSD.i#"]

results = {}
for sym in symbols:
    if sym == "XAUUSD":
        candidates = gold_aliases
    else:
        candidates = [sym, f"{sym}#", f"{sym}.i#", f"{sym}.i", f"{sym}m", f"{sym}.m"]

    resolved = None
    for cand in candidates:
        info = mt5.symbol_info(cand)
        if info is not None and info.trade_mode == 4:
            mt5.symbol_select(cand, True)
            resolved = cand
            break

    if resolved is None:
        for cand in candidates:
            info = mt5.symbol_info(cand)
            if info is not None:
                mt5.symbol_select(cand, True)
                resolved = cand
                break

    if resolved is None:
        all_syms = mt5.symbols_get()
        if all_syms:
            search = ["GOLD", "XAUUSD"] if sym == "XAUUSD" else [sym]
            for s in all_syms:
                if any(t in s.name.upper() for t in search):
                    info = mt5.symbol_info(s.name)
                    if info is not None:
                        mt5.symbol_select(s.name, True)
                        resolved = s.name
                        break

    if resolved:
        info = mt5.symbol_info(resolved)
        tick = mt5.symbol_info_tick(resolved)
        if tick and tick.ask > 0 and tick.bid > 0:
            spread_raw = tick.ask - tick.bid
            
            # Correct pip calculation
            if info.digits == 5:
                spread_pips = spread_raw / 0.0001
            elif info.digits == 3:
                spread_pips = spread_raw / 0.01
            elif info.digits == 2:
                # Check if it's crypto (contract_size=1) or commodity/index
                if info.trade_contract_size <= 1.0:
                    spread_pips = spread_raw / 1.0  # Crypto: 1 pip = 1.0
                else:
                    spread_pips = spread_raw / 1.0  # Gold/Indices: 1 pip = 1 point
            else:
                spread_pips = spread_raw / info.point
            
            results[sym] = {
                "resolved": resolved,
                "digits": info.digits,
                "point": info.point,
                "spread_raw": round(spread_raw, 6),
                "spread_pips": round(spread_pips, 2),
                "ask": tick.ask,
                "bid": tick.bid,
                "trade_mode": info.trade_mode,
                "volume_min": info.volume_min,
                "volume_step": info.volume_step,
                "contract_size": info.trade_contract_size,
            }
            mode_str = "FULL" if info.trade_mode == 4 else f"MODE={info.trade_mode}"
            print(f"  {sym:<10} -> {resolved:<15} | Spread: {spread_pips:>6.2f} pips | Ask: {tick.ask} | Bid: {tick.bid} | {mode_str}")
        else:
            print(f"  {sym:<10} -> {resolved:<15} | Market closed")
    else:
        print(f"  {sym:<10} -> NOT FOUND")

mt5.shutdown()

with open("actual_spreads.json", "w") as f:
    json.dump(results, f, indent=2)

# Print comparison with configured spreads
print("\n" + "=" * 80)
print("COMPARISON: Actual XM Spreads vs Configured symbol_profiles.json")
print("=" * 80)
print(f"{'Symbol':<10} | {'Actual':<10} | {'Configured':<12} | {'Status':<10}")
print("-" * 50)

with open("config/symbol_profiles.json") as f:
    profiles = json.load(f)

for sym, data in results.items():
    actual = data["spread_pips"]
    # Find configured spread
    configured = None
    for key, profile in profiles.items():
        if sym in key or key in sym:
            configured = profile.get("max_spread_pips")
            break
    if configured is None:
        configured = "N/A"
        status = "MISSING"
    elif actual <= configured:
        status = "OK"
    else:
        status = f"WIDE (+{actual - configured:.1f})"
    print(f"  {sym:<10} | {actual:>6.2f} pips | {configured} pips | {status}")
