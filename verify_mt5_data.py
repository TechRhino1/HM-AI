"""Verify MT5 data availability for 6-month backtest"""
import MetaTrader5 as mt5

mt5.initialize()
info = mt5.account_info()
print(f"Server: {info.server} | Balance: ${info.balance:,.2f} | Leverage: 1:{info.leverage}")

symbols = ["GOLD.i#", "BTCUSD#", "EURUSD", "GBPUSD"]
for sym in symbols:
    rates = mt5.copy_rates_from_pos(sym, 16385, 1, 4380)  # H1, 6 months
    if rates is not None and len(rates) > 0:
        from datetime import datetime
        first = datetime.fromtimestamp(rates[0]['time'])
        last = datetime.fromtimestamp(rates[-1]['time'])
        bars = len(rates)
        days = (last - first).days
        print(f"  {sym:<12} | {bars} H1 bars | {first:%Y-%m-%d} to {last:%Y-%m-%d} ({days} days)")
    else:
        rates2 = mt5.copy_rates_from_pos(sym, 16385, 1, 100)
        cnt = len(rates2) if rates2 is not None else 0
        print(f"  {sym:<12} | FAILED to get 4380 bars (got {cnt})")

mt5.shutdown()
