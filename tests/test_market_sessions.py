"""Tests for jarvis.market.sessions.SessionEngine.

This module had no tests at all, yet it decides three things that gate real
trades: whether the market is open (`get_market_trading_status`, used as
`is_mkt_open` in decision_engine), whether a Forex entry is allowed
(`is_forex_killzone_active`, documented as a *hard filter*), and whether an
index entry is allowed (`is_index_prime_session`). It also produces
`is_in_killzone`, which `online_ml_predictor` turns into a learned feature.

Every window below is taken from the module's own docstrings and comments. Where
a test contradicts the code rather than the other way round, the test names the
contract it is enforcing in its docstring.
"""

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.data.schemas import SessionContext
from jarvis.market.sessions import SessionEngine

# Fixed calendar anchors so no test depends on the day it runs.
MON = datetime(2026, 1, 5, tzinfo=timezone.utc)    # Monday
WED = datetime(2026, 1, 7, tzinfo=timezone.utc)    # Wednesday
THU = datetime(2026, 1, 8, tzinfo=timezone.utc)    # Thursday
FRI = datetime(2026, 1, 2, tzinfo=timezone.utc)    # Friday
SAT = datetime(2026, 1, 3, tzinfo=timezone.utc)    # Saturday
SUN = datetime(2026, 1, 4, tzinfo=timezone.utc)    # Sunday

IST = timezone(timedelta(hours=5, minutes=30))


def at(day: datetime, hour: int, minute: int = 0, tz=timezone.utc) -> datetime:
    """A concrete instant on `day` at HH:MM in `tz`."""
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)


def naive(day: datetime, hour: int, minute: int = 0) -> datetime:
    """The same instant with no tzinfo — must be read as UTC."""
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)


# ---------------------------------------------------------------------------
# Guard the calendar anchors themselves. If these are wrong every session test
# below is silently testing the wrong day of the week.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "day,expected",
    [(MON, 0), (WED, 2), (THU, 3), (FRI, 4), (SAT, 5), (SUN, 6)],
    ids=["MON", "WED", "THU", "FRI", "SAT", "SUN"],
)
def test_calendar_anchors_are_the_weekday_they_claim(day, expected):
    assert day.weekday() == expected


# ---------------------------------------------------------------------------
# get_current_session
# ---------------------------------------------------------------------------

class TestGetCurrentSession:
    @pytest.mark.parametrize(
        "hour,expected",
        [
            (0, "ASIAN"), (3, "ASIAN"), (6, "ASIAN"),
            (7, "LONDON"), (9, "LONDON"), (11, "LONDON"),
            (12, "LONDON_NY_OVERLAP"), (14, "LONDON_NY_OVERLAP"), (15, "LONDON_NY_OVERLAP"),
            (16, "NEW_YORK"), (18, "NEW_YORK"), (20, "NEW_YORK"),
            (21, "OFF_HOURS"), (23, "OFF_HOURS"),
        ],
    )
    def test_every_hour_of_the_trading_day_maps_to_one_session(self, hour, expected):
        ctx = SessionEngine.get_current_session(at(WED, hour))
        assert ctx.current_session == expected

    def test_all_24_hours_are_covered_with_no_gap(self):
        seen = {SessionEngine.get_current_session(at(WED, h)).current_session for h in range(24)}
        assert seen == {"ASIAN", "LONDON", "LONDON_NY_OVERLAP", "NEW_YORK", "OFF_HOURS"}

    def test_returns_a_session_context(self):
        assert isinstance(SessionEngine.get_current_session(at(WED, 10)), SessionContext)

    def test_utc_hour_and_day_of_week_are_populated(self):
        ctx = SessionEngine.get_current_session(at(THU, 13))
        assert (ctx.utc_hour, ctx.day_of_week) == (13, 3)

    def test_naive_datetime_is_read_as_utc(self):
        assert SessionEngine.get_current_session(naive(WED, 9)).current_session == "LONDON"

    def test_aware_datetime_in_another_zone_is_converted_to_utc(self):
        """`utc_hour` must actually be UTC.

        13:30 IST is 08:00 UTC. Reading `.hour` off the passed datetime instead
        of converting would report 13 — NEW_YORK — for what is the London
        session, and would drift by the whole offset for every consumer that
        names the field `utc_hour`.
        """
        ctx = SessionEngine.get_current_session(at(WED, 13, 30, tz=IST))
        assert ctx.utc_hour == 8
        assert ctx.current_session == "LONDON"

    @pytest.mark.parametrize("hour", [0, 7, 12, 16, 21])
    def test_aware_and_naive_agree_when_the_zone_is_utc(self, hour):
        assert (
            SessionEngine.get_current_session(at(WED, hour)).current_session
            == SessionEngine.get_current_session(naive(WED, hour)).current_session
        )

    # --- is_prime_session ---------------------------------------------------

    @pytest.mark.parametrize("hour", [7, 9, 12, 15, 19, 20])
    def test_prime_during_weekday_liquidity_hours(self, hour):
        assert SessionEngine.get_current_session(at(WED, hour)).is_prime_session is True

    @pytest.mark.parametrize("hour", [0, 6, 21, 22, 23])
    def test_not_prime_outside_them(self, hour):
        assert SessionEngine.get_current_session(at(WED, hour)).is_prime_session is False

    def test_prime_window_is_inclusive_at_both_ends(self):
        assert SessionEngine.get_current_session(at(WED, 7)).is_prime_session is True
        assert SessionEngine.get_current_session(at(WED, 20, 59)).is_prime_session is True
        assert SessionEngine.get_current_session(at(WED, 21)).is_prime_session is False

    @pytest.mark.parametrize("hour", [0, 8, 12, 16, 20])
    @pytest.mark.parametrize("day", [SAT, SUN], ids=["SAT", "SUN"])
    def test_never_prime_at_the_weekend(self, day, hour):
        assert SessionEngine.get_current_session(at(day, hour)).is_prime_session is False

    @pytest.mark.parametrize("hour", [8, 12, 16])
    def test_weekend_is_off_hours_not_a_named_session(self, hour):
        """Saturday 12:00 UTC is not a London/NY overlap — there is no session.

        `is_prime_session` already carries the weekend, so this is about the
        *string*, which is printed into analyst narratives and the copilot
        context. Reporting "LONDON_NY_OVERLAP" on a Saturday is wrong there.
        """
        for day in (SAT, SUN):
            ctx = SessionEngine.get_current_session(at(day, hour))
            assert ctx.current_session == "OFF_HOURS", f"{day:%A} {hour}:00"


# ---------------------------------------------------------------------------
# killzones
# ---------------------------------------------------------------------------

class TestKillzones:
    @pytest.mark.parametrize(
        "hour,expected",
        [
            (6, None),
            (7, "LONDON_OPEN"), (8, "LONDON_OPEN"), (9, "LONDON_OPEN"),
            (10, None), (11, None),
            (12, "NY_OPEN"), (13, "NY_OPEN"), (14, "NY_OPEN"),
            (15, "LONDON_CLOSE"), (16, "LONDON_CLOSE"),
            (17, None), (21, None), (23, None),
        ],
    )
    def test_window_boundaries(self, hour, expected):
        assert SessionEngine.get_active_killzone(at(WED, hour))["active_killzone"] == expected

    def test_is_in_killzone_mirrors_active_killzone(self):
        for hour in range(24):
            kz = SessionEngine.get_active_killzone(at(WED, hour))
            assert kz["is_in_killzone"] is (kz["active_killzone"] is not None)

    @pytest.mark.parametrize(
        "hour,minute,expected",
        [
            (7, 0, 180),    # LONDON_OPEN, three hours left
            (9, 30, 30),    # ...half an hour left
            (9, 59, 1),
            (12, 0, 180),   # NY_OPEN
            (15, 0, 120),   # LONDON_CLOSE, two hours
            (16, 45, 15),
        ],
    )
    def test_minutes_remaining_counts_down_to_the_end(self, hour, minute, expected):
        kz = SessionEngine.get_active_killzone(at(WED, hour, minute))
        assert kz["killzone_minutes_remaining"] == expected

    def test_minutes_remaining_is_zero_when_no_killzone(self):
        assert SessionEngine.get_active_killzone(at(WED, 11))["killzone_minutes_remaining"] == 0

    def test_minutes_remaining_is_never_negative(self):
        for hour in range(24):
            for minute in (0, 30, 59):
                assert SessionEngine.get_active_killzone(at(WED, hour, minute))[
                    "killzone_minutes_remaining"
                ] >= 0

    @pytest.mark.parametrize("hour", [0, 3, 6])
    def test_asian_range(self, hour):
        assert SessionEngine.get_active_killzone(at(WED, hour))["is_asian_range"] is True

    @pytest.mark.parametrize("hour", [7, 12, 16, 23])
    def test_outside_asian_range(self, hour):
        assert SessionEngine.get_active_killzone(at(WED, hour))["is_asian_range"] is False

    @pytest.mark.parametrize("hour", [0, 3, 6])
    @pytest.mark.parametrize("day", [SAT, SUN], ids=["SAT", "SUN"])
    def test_no_asian_range_at_the_weekend(self, day, hour):
        """There is no Asian accumulation range on a Saturday either."""
        assert SessionEngine.get_active_killzone(at(day, hour))["is_asian_range"] is False

    def test_the_asian_range_does_not_overlap_any_killzone(self):
        """`is_asian_range` and `is_in_killzone` must never both be true."""
        for hour in range(24):
            kz = SessionEngine.get_active_killzone(at(WED, hour))
            assert not (kz["is_asian_range"] and kz["is_in_killzone"])

    def test_killzone_definitions_are_well_formed(self):
        for name, (start, end) in SessionEngine.KILLZONES.items():
            assert 0 <= start < end <= 24, name

    def test_killzone_windows_do_not_overlap_each_other(self):
        """Dict iteration + `break` silently resolves overlaps by insertion order.

        That is invisible today, but adding a window would make one of them
        unreachable with no error, so lock the invariant instead of the order.
        """
        spans = sorted(SessionEngine.KILLZONES.values())
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            assert e1 <= s2, f"{spans} overlap"

    def test_every_hour_maps_to_at_most_one_killzone(self):
        for hour in range(24):
            hits = [n for n, (s, e) in SessionEngine.KILLZONES.items() if s <= hour < e]
            assert len(hits) <= 1, f"hour {hour} is in {hits}"

    def test_naive_datetime_is_read_as_utc(self):
        assert SessionEngine.get_active_killzone(naive(WED, 8))["active_killzone"] == "LONDON_OPEN"

    def test_aware_datetime_in_another_zone_is_converted_to_utc(self):
        """13:30 IST is 08:00 UTC — LONDON_OPEN, not the 13:00 NY_OPEN window."""
        kz = SessionEngine.get_active_killzone(at(WED, 13, 30, tz=IST))
        assert kz["active_killzone"] == "LONDON_OPEN"
        assert kz["killzone_minutes_remaining"] == 120

    @pytest.mark.parametrize("hour", [7, 8, 12, 15])
    @pytest.mark.parametrize("day", [SAT, SUN], ids=["SAT", "SUN"])
    def test_no_killzone_at_the_weekend(self, day, hour):
        """`is_forex_killzone_active` is documented as a hard Forex entry filter.

        Every other time gate in this module carries a weekday condition; the
        killzones did not. `is_in_killzone` is also written into the ML
        feature vector, so a weekend bar would have trained `is_killzone=1`
        on a closed market.
        """
        kz = SessionEngine.get_active_killzone(at(day, hour))
        assert kz["active_killzone"] is None
        assert kz["is_in_killzone"] is False
        assert SessionEngine.is_forex_killzone_active(at(day, hour)) is False


class TestIsForexKillzoneActive:
    def test_matches_the_killzone_dict(self):
        for hour in range(24):
            assert SessionEngine.is_forex_killzone_active(at(WED, hour)) == (
                SessionEngine.get_active_killzone(at(WED, hour))["is_in_killzone"]
            )

    def test_true_inside_a_window(self):
        assert SessionEngine.is_forex_killzone_active(at(WED, 13)) is True

    def test_false_outside_every_window(self):
        assert SessionEngine.is_forex_killzone_active(at(WED, 11)) is False


# ---------------------------------------------------------------------------
# is_index_prime_session
# ---------------------------------------------------------------------------

class TestIsIndexPrimeSession:
    @pytest.mark.parametrize("hour,minute,expected", [
        (13, 59, False),
        (14, 0, True),
        (17, 30, True),
        (19, 59, True),
        (20, 0, False),
        (2, 0, False),
    ])
    def test_cash_market_core_liquidity_window(self, hour, minute, expected):
        assert SessionEngine.is_index_prime_session(at(WED, hour, minute)) is expected

    @pytest.mark.parametrize("hour", [9, 14, 18])
    @pytest.mark.parametrize("day", [SAT, SUN], ids=["SAT", "SUN"])
    def test_never_prime_at_the_weekend(self, day, hour):
        assert SessionEngine.is_index_prime_session(at(day, hour)) is False

    def test_naive_datetime_is_read_as_utc(self):
        assert SessionEngine.is_index_prime_session(naive(WED, 15)) is True

    def test_aware_datetime_in_another_zone_is_converted_to_utc(self):
        """20:15 IST is 14:45 UTC — inside the window; 14:15 IST would not be."""
        assert SessionEngine.is_index_prime_session(at(WED, 20, 15, tz=IST)) is True
        assert SessionEngine.is_index_prime_session(at(WED, 14, 15, tz=IST)) is False


# ---------------------------------------------------------------------------
# get_market_trading_status
# ---------------------------------------------------------------------------

CORE_KEYS = {
    "symbol", "is_open", "market_type", "status", "status_badge", "status_text",
    "next_event", "next_open_ist", "next_open_utc", "countdown_seconds",
    "countdown_formatted", "reason",
}


class TestCryptoStatus:
    @pytest.mark.parametrize("symbol", ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"])
    def test_always_open(self, symbol):
        for day in (FRI, SAT, SUN, MON):
            for hour in (3, 12, 21, 23):
                assert SessionEngine.get_market_trading_status(symbol, at(day, hour))["is_open"] is True

    def test_closed_weekend_window_still_open(self):
        """The exact instant Forex is shut: Saturday 12:00 UTC."""
        assert SessionEngine.get_market_trading_status("BTCUSD", at(SAT, 12))["is_open"] is True

    def test_market_type_and_no_countdown(self):
        s = SessionEngine.get_market_trading_status("BTCUSD", at(SAT, 12))
        assert s["market_type"] == "CRYPTO_24_7"
        assert s["status"] == "OPEN"
        assert s["countdown_seconds"] == 0
        assert s["countdown_formatted"] == "Live Now"

    def test_symbol_is_echoed_unchanged(self):
        assert SessionEngine.get_market_trading_status("BTCUSD", at(MON, 9))["symbol"] == "BTCUSD"

    def test_lowercase_symbol_is_still_recognised(self):
        assert SessionEngine.get_market_trading_status("btcusd", at(SAT, 12))["is_open"] is True


class TestForexWeekendSchedule:
    @pytest.mark.parametrize("symbol", ["EURUSD", "XAUUSD", "GBPJPY"])
    def test_closed_from_friday_2100_utc(self, symbol):
        assert SessionEngine.get_market_trading_status(symbol, at(FRI, 21))["is_open"] is False

    def test_still_open_one_minute_earlier(self):
        assert SessionEngine.get_market_trading_status("EURUSD", at(FRI, 20, 59))["is_open"] is True

    @pytest.mark.parametrize("hour", [0, 6, 12, 23])
    def test_closed_all_saturday(self, hour):
        assert SessionEngine.get_market_trading_status("EURUSD", at(SAT, hour))["is_open"] is False

    def test_closed_sunday_until_2100(self):
        assert SessionEngine.get_market_trading_status("EURUSD", at(SUN, 20, 59))["is_open"] is False

    def test_open_from_sunday_2100(self):
        assert SessionEngine.get_market_trading_status("EURUSD", at(SUN, 21))["is_open"] is True

    @pytest.mark.parametrize("hour", [0, 12, 20, 23])
    @pytest.mark.parametrize("day", [MON, WED, THU], ids=["MON", "WED", "THU"])
    def test_open_throughout_the_week(self, day, hour):
        assert SessionEngine.get_market_trading_status("EURUSD", at(day, hour))["is_open"] is True

    def test_closed_status_fields(self):
        s = SessionEngine.get_market_trading_status("EURUSD", at(SAT, 12))
        assert s["status"] == "CLOSED_WEEKEND"
        assert s["market_type"] == "FOREX_METALS_24_5"
        assert "weekend" in s["reason"].lower()
        assert s["countdown_seconds"] > 0

    def test_open_status_fields(self):
        s = SessionEngine.get_market_trading_status("EURUSD", at(WED, 12))
        assert s["status"] == "OPEN"
        assert s["next_open_ist"] == "Currently Open"
        assert s["next_open_utc"] == ""


class TestNextOpenCountdown:
    @pytest.mark.parametrize(
        "day,hour,expected_weekday",
        [
            (FRI, 21, 6), (FRI, 23, 6),      # Friday evening -> Sunday
            (SAT, 0, 6), (SAT, 12, 6),       # Saturday -> Sunday
            (SUN, 0, 6), (SUN, 20, 6),       # Sunday -> later that Sunday
        ],
    )
    def test_next_open_is_sunday_2100_utc(self, day, hour, expected_weekday):
        s = SessionEngine.get_market_trading_status("EURUSD", at(day, hour))
        nxt = datetime.fromisoformat(s["next_open_utc"])
        assert nxt.weekday() == expected_weekday
        assert (nxt.hour, nxt.minute) == (21, 0)
        assert nxt > at(day, hour)

    def test_countdown_matches_next_open(self):
        now = at(SAT, 12)
        s = SessionEngine.get_market_trading_status("EURUSD", now)
        expected = (datetime.fromisoformat(s["next_open_utc"]) - now).total_seconds()
        assert s["countdown_seconds"] == int(expected)

    def test_countdown_is_never_negative(self):
        for hour in range(24):
            for day in (FRI, SAT, SUN):
                assert SessionEngine.get_market_trading_status("EURUSD", at(day, hour))[
                    "countdown_seconds"
                ] >= 0

    def test_friday_2100_countdown_is_two_days(self):
        s = SessionEngine.get_market_trading_status("EURUSD", at(FRI, 21))
        assert s["countdown_seconds"] == 2 * 24 * 3600
        assert s["countdown_formatted"] == "2d 0h 0m"

    def test_countdown_format_uses_days_when_over_a_day(self):
        assert "d" in SessionEngine.get_market_trading_status("EURUSD", at(SAT, 12))["countdown_formatted"]

    def test_countdown_format_uses_hours_when_under_a_day(self):
        s = SessionEngine.get_market_trading_status("EURUSD", at(SUN, 20))
        assert "d" not in s["countdown_formatted"]
        assert s["countdown_formatted"] == "1h 0m"

    def test_next_open_ist_is_five_and_a_half_hours_ahead(self):
        s = SessionEngine.get_market_trading_status("EURUSD", at(SAT, 12))
        nxt_utc = datetime.fromisoformat(s["next_open_utc"])
        nxt_ist = nxt_utc.astimezone(IST)
        assert s["next_open_ist"] == nxt_ist.strftime("%a %b %d, %I:%M %p IST")
        assert "IST" in s["next_open_ist"]


class TestNextCloseCountdown:
    def test_next_close_is_friday_2100_utc(self):
        """A Monday bar counts down to Friday 21:00 UTC = Saturday 02:30 IST."""
        s = SessionEngine.get_market_trading_status("EURUSD", at(MON, 0))
        expected_ist = datetime(2026, 1, 9, 21, 0, tzinfo=timezone.utc).astimezone(IST)
        assert s["next_event"] == f"Closes {expected_ist.strftime('%a %b %d, %I:%M %p IST')}"
        assert s["countdown_seconds"] == (4 * 24 + 21) * 3600

    def test_friday_morning_counts_down_to_that_evening(self):
        now = at(FRI, 12)
        s = SessionEngine.get_market_trading_status("EURUSD", now)
        assert s["countdown_seconds"] == 9 * 3600

    def test_sunday_evening_counts_down_to_next_friday(self):
        now = at(SUN, 22)
        s = SessionEngine.get_market_trading_status("EURUSD", now)
        assert s["is_open"] is True
        # Sunday 22:00 -> Friday 21:00 is 4 days 23 hours.
        assert s["countdown_seconds"] == (4 * 24 + 23) * 3600

    def test_open_branch_countdown_format_groups_full_days(self):
        """Monday 00:00 UTC is 117 hours from Friday 21:00 — 4d 21h, not 9d.

        Both branches format this independently, so the open one needs its own
        check; only the weekend branch was pinned.
        """
        s = SessionEngine.get_market_trading_status("EURUSD", at(MON, 0))
        assert s["countdown_formatted"] == "4d 21h 0m"

    def test_countdown_is_never_negative_when_open(self):
        for hour in range(24):
            for day in (MON, WED, THU, FRI, SUN):
                assert SessionEngine.get_market_trading_status("EURUSD", at(day, hour))[
                    "countdown_seconds"
                ] >= 0


class TestStatusShape:
    @pytest.mark.parametrize(
        "day,hour", [(WED, 12), (SAT, 12), (SUN, 3), (FRI, 22)],
        ids=["weekday", "saturday", "sunday", "friday-night"],
    )
    def test_core_keys_present_in_every_branch(self, day, hour):
        assert CORE_KEYS <= set(SessionEngine.get_market_trading_status("EURUSD", at(day, hour)))

    def test_crypto_branch_also_exposes_the_core_keys(self):
        assert CORE_KEYS <= set(SessionEngine.get_market_trading_status("BTCUSD", at(SAT, 12)))

    @pytest.mark.parametrize("symbol", [None, "", "   "])
    def test_empty_symbol_does_not_crash(self, symbol):
        s = SessionEngine.get_market_trading_status(symbol, at(WED, 12))
        assert s["is_open"] is True

    @pytest.mark.parametrize("symbol", ["EURUSD", "eurusd", "USDJPY"])
    def test_forex_symbols_are_not_mistaken_for_crypto(self, symbol):
        assert SessionEngine.get_market_trading_status(symbol, at(SAT, 12))["is_open"] is False

    @pytest.mark.parametrize("symbol", ["US30", "NAS100", "US500", "GER40"])
    def test_index_symbols_are_not_mistaken_for_crypto(self, symbol):
        s = SessionEngine.get_market_trading_status(symbol, at(WED, 12))
        assert s["market_type"] == "FOREX_METALS_24_5"

    def test_naive_datetime_is_read_as_utc(self):
        assert SessionEngine.get_market_trading_status("EURUSD", naive(FRI, 22))["is_open"] is False

    def test_aware_datetime_in_another_zone_is_converted_to_utc(self):
        """Two instants where reading the wall clock as UTC gives the wrong answer.

        Friday 23:00 IST is 17:30 UTC — open; as a raw UTC hour it is past the
        21:00 Friday close. Monday 02:00 IST is Sunday 20:30 UTC — still shut;
        as a raw UTC hour it is mid-week and open.
        """
        assert SessionEngine.get_market_trading_status("EURUSD", at(FRI, 23, 0, tz=IST))["is_open"] is True
        assert SessionEngine.get_market_trading_status("EURUSD", at(MON, 2, 0, tz=IST))["is_open"] is False


# ---------------------------------------------------------------------------
# Cross-function consistency
# ---------------------------------------------------------------------------

class TestCrossFunctionConsistency:
    def test_killzone_hours_are_always_inside_the_prime_window(self):
        """A killzone must never fire when `is_prime_session` is False."""
        for hour in range(24):
            if SessionEngine.is_forex_killzone_active(at(WED, hour)):
                assert SessionEngine.get_current_session(at(WED, hour)).is_prime_session is True

    def test_index_prime_window_lies_inside_the_market_open_window(self):
        for hour in range(24):
            if SessionEngine.is_index_prime_session(at(WED, hour)):
                assert SessionEngine.get_market_trading_status("US30", at(WED, hour))["is_open"] is True

    def test_a_naive_and_aware_input_agree_on_market_status(self):
        for hour in (0, 12, 21, 23):
            assert (
                SessionEngine.get_market_trading_status("EURUSD", at(FRI, hour))["is_open"]
                == SessionEngine.get_market_trading_status("EURUSD", naive(FRI, hour))["is_open"]
            )

    def test_session_and_status_agree_that_the_weekend_is_not_tradeable(self):
        """Saturday midday: no prime session, no killzone, market shut."""
        now = at(SAT, 12)
        assert SessionEngine.get_current_session(now).is_prime_session is False
        assert SessionEngine.is_forex_killzone_active(now) is False
        assert SessionEngine.get_market_trading_status("EURUSD", now)["is_open"] is False
