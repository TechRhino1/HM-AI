"""
HM Algo 2.0 — Drawdown & Daily Loss Monitoring Engine.
Enforces hard daily loss caps and maximum portfolio drawdown limits to guarantee capital preservation.

A loss is NEVER re-anchored away
--------------------------------
This guard previously treated any single observation more than 33.3% below a
recorded baseline as a withdrawal and re-anchored to it (`baseline > current * 1.5`).
That is indistinguishable from a crash, so the worse the loss the safer the guard
thought things were: a 40% intraday drop reported `daily_loss_pct == 0.0` and
`passed == True`, and the same re-anchor also erased `peak_equity`, cancelling the
portfolio circuit breaker. `risk_engine` gates three separate checks on `passed`,
the last of which returns `authorized: True`, so the account kept opening new
positions through a 40% drawdown.

No single-instant test can separate the two cases: a flat account that REALISED a
40% loss has equity == balance and looks exactly like one that had a withdrawal.
So this guard no longer guesses. A big drop is reported as the breach it is, and a
genuine withdrawal or deposit is re-anchored explicitly via `reset_baselines()`.
Failing closed costs a day of trading; failing open costs the account.
"""
import sqlite3
import os
from typing import Dict, Any, Optional, Callable
from datetime import datetime, timezone

from jarvis.config.paths import resolve_db_path
from jarvis.data.schema_version import ensure_version

# D3: 1 = `drawdown_state` as it exists today.
SCHEMA_VERSION = 1

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class DrawdownGuard:
    def __init__(self, max_daily_loss_pct: float = 4.0, max_total_drawdown_pct: float = 10.0, db_path: str = "jarvis_drawdown_state.db", clock: Optional[Callable[[], datetime]] = None):
        # Injectable clock, same convention as circuit_breaker: live trading uses
        # the real wall clock, a backtest advances on bar time. NOTE the day
        # boundary below is UTC, not the broker's trading day — see set_clock().
        self._now = clock if clock is not None else _utc_now
        # Anchored on the repo data dir — see jarvis.config.paths.
        db_path = resolve_db_path(db_path)
        # Hermetic backtesting: in memory, nothing persisted. A backtest that
        # ends mid-drawdown otherwise leaves daily_start_equity/peak_equity
        # behind and the next run starts partially through a drawdown it never
        # actually had, tripping the daily-loss cap early. See the matching note
        # in circuit_breaker.py. "" is the documented in-memory sentinel.
        try:
            from jarvis.config.runtime import is_offline

            if is_offline():
                db_path = ""
        except Exception:
            pass
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_total_drawdown_pct = max_total_drawdown_pct
        self.daily_start_equity: float = 0.0
        self.peak_equity: float = 0.0
        self.db_path = db_path
        # Day the in-memory baselines belong to; a change rolls the daily cap over.
        self._last_seen_date = self._today()
        if self.db_path:
            self._init_db()
            self._load_state()

    def set_clock(self, clock: Optional[Callable[[], datetime]]) -> None:
        """Inject the source of "today" (backtests pass bar time; live passes None -> UTC).

        The daily-loss cap resets on a UTC date change, which is NOT the broker's
        trading day — a server on GMT+2/+3 rolls over two or three hours into the
        session. Pass a broker-day clock here to align them.
        """
        self._now = clock if clock is not None else _utc_now

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _init_db(self):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS drawdown_state (
                        id INTEGER PRIMARY KEY,
                        daily_start_equity REAL,
                        peak_equity REAL,
                        last_saved_date TEXT
                    )
                ''')
                # D3: record the shape of this file.
                ensure_version(conn, SCHEMA_VERSION, "drawdown_state")
        finally:
            conn.close()

    def _load_state(self):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                cursor = conn.execute('SELECT daily_start_equity, peak_equity, last_saved_date FROM drawdown_state WHERE id = 1')
                row = cursor.fetchone()
                if row:
                    self.daily_start_equity, self.peak_equity, last_saved_date_str = row
                    
                    # Check for daily reset
                    if last_saved_date_str != self._today():
                        self.daily_start_equity = 0.0
                        self._save_state()
                else:
                    conn.execute('INSERT INTO drawdown_state (id, daily_start_equity, peak_equity, last_saved_date) VALUES (1, 0.0, 0.0, ?)',
                                (self._today(),))
        finally:
            conn.close()

    def _save_state(self):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                conn.execute('''
                    UPDATE drawdown_state
                    SET daily_start_equity = ?, peak_equity = ?, last_saved_date = ?
                    WHERE id = 1
                ''', (self.daily_start_equity, self.peak_equity, self._today()))
        finally:
            conn.close()

    def reset_baselines(self, current_equity: float) -> None:
        """Re-anchor both baselines to `current_equity` after a real deposit/withdrawal.

        This is the ONLY supported way to move a baseline down. See the module
        docstring: the guard cannot tell a withdrawal from a crash, so it does not
        try, and an operator who just moved money must say so explicitly.
        """
        self.daily_start_equity = float(current_equity)
        self.peak_equity = float(current_equity)
        self._save_state()

    def update_equity_benchmarks(self, current_equity: float, current_balance: float):
        changed = False

        # True daily reset check in case process stays open across midnight.
        #
        # This used to be gated on `if self.db_path:` and read `last_saved_date`
        # back out of SQLite, which meant an IN-MEMORY guard — the documented
        # hermetic-backtest path (`is_offline()` forces db_path="") — never reset
        # its daily baseline at all, however far the clock advanced. A backtest
        # spanning many days would therefore measure every day's loss against
        # day one's equity, and once the cap tripped it stayed tripped for the
        # rest of the run. Tracking the day in memory fixes that and removes a
        # SQLite round-trip from a path risk_engine calls three times a decision.
        current_date = self._today()
        if current_date != self._last_seen_date:
            self._last_seen_date = current_date
            self.daily_start_equity = 0.0
            changed = True

        # Daily-loss baseline must be tracked on EQUITY consistently (not balance),
        # otherwise open positions make the daily-loss figure wrong.
        #
        # Both baselines move UP only (or from an unset/zeroed state). They are
        # deliberately never lowered on a large drop: `daily_start_equity >
        # current_equity * 1.5` used to re-anchor here on the assumption that only
        # a withdrawal could move equity that far, but a 33.4%+ intraday loss moves
        # it just as far, and re-anchoring reports that loss as 0% and lets the
        # account keep trading. A crash must look like a crash. `current_balance`
        # cannot separate the cases (a REALISED loss lowers balance identically to
        # a withdrawal), which is why this no longer tries — use reset_baselines().
        if self.daily_start_equity <= 0:
            self.daily_start_equity = current_equity
            changed = True
        if self.peak_equity <= 0 or current_equity > self.peak_equity:
            self.peak_equity = current_equity
            changed = True
            
        if changed and self.db_path:
            self._save_state()

    def get_risk_multiplier(self, current_equity: float) -> float:
        if self.peak_equity <= 0:
            return 1.0
        
        dd_pct = max(0.0, ((self.peak_equity - current_equity) / self.peak_equity) * 100.0)
        
        if dd_pct < 3.0:
            return 1.0
        elif dd_pct < 5.0:
            return 0.75
        elif dd_pct < 8.0:
            return 0.50
        else:
            return 0.0

    def check_limits(self, current_equity: float, current_balance: float) -> Dict[str, Any]:
        self.update_equity_benchmarks(current_equity, current_balance)

        # Daily loss check
        daily_loss_pct = 0.0
        if self.daily_start_equity > 0:
            daily_loss_pct = max(0.0, ((self.daily_start_equity - current_equity) / self.daily_start_equity) * 100.0)

        # Max drawdown check
        total_dd_pct = 0.0
        if self.peak_equity > 0:
            total_dd_pct = max(0.0, ((self.peak_equity - current_equity) / self.peak_equity) * 100.0)

        breaches = []
        if daily_loss_pct >= self.max_daily_loss_pct:
            breaches.append(f"Max Daily Loss breached ({daily_loss_pct:.2f}% >= {self.max_daily_loss_pct:.2f}%). Trading halted for today.")
        if total_dd_pct >= self.max_total_drawdown_pct:
            breaches.append(f"Max Portfolio Drawdown breached ({total_dd_pct:.2f}% >= {self.max_total_drawdown_pct:.2f}%). Circuit breaker triggered.")

        return {
            "passed": len(breaches) == 0,
            "daily_loss_pct": round(daily_loss_pct, 2),
            "total_dd_pct": round(total_dd_pct, 2),
            "breaches": breaches
        }
