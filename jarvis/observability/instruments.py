"""The catalogue of metrics this platform publishes.

WHY THIS FILE EXISTS
--------------------
Metrics declared next to their call site are easy to write and impossible to
find: "what does the platform publish?" becomes a grep, and two modules will
declare `orders_total` with different labels and only find out at scrape time.
So every metric lives here, and instrumented modules import from here.

Each entry states what it counts, which labels it carries, and — where it
matters — what a rising value actually means. A counter nobody can interpret is
noise with a cost.

This module is a leaf: it imports nothing from `jarvis.*`.
"""
from __future__ import annotations

from .metrics import Counter, Gauge, Histogram, counter as _counter, gauge as _gauge, histogram as _histogram

# ── Decision evaluation ─────────────────────────────────────────────────────
#: One per `DecisionEngine.evaluate` call. `outcome` is the caller's view of
#: what came back: `trade` (authorized), `no_trade` (a decision was reached and
#: it said no), `refused` (no decision at all — stale or synthetic data) or
#: `error` (the engine raised). A rising `error` means the platform is silently
#: trading nothing, which looks exactly like a quiet market.
DECISION_EVALUATIONS: Counter = _counter(
    "jarvis_decision_evaluations_total",
    "Decisions evaluated, by symbol, trade style and outcome.",
    ("style", "outcome"),
)

#: How long the evaluation itself took. This is the number that decides whether
#: the analyst cluster's wall-clock budget is being eaten by the engine or by
#: the fetch in front of it.
DECISION_LATENCY: Histogram = _histogram(
    "jarvis_decision_evaluation_seconds",
    "Wall-clock duration of one decision evaluation.",
    ("style",),
)

# ── Order submission ────────────────────────────────────────────────────────
#: One per dispatch attempt, labelled by the broker result.
#:
#: A REFUSED order is answered with HTTP 200 elsewhere in this codebase, so the
#: response *status code* is not evidence that money moved. This counter is:
#: `filled` means a ticket exists.
ORDERS_SUBMITTED: Counter = _counter(
    "jarvis_orders_submitted_total",
    "Order dispatch attempts, by execution mode and broker result.",
    ("mode", "status"),
)

ORDER_LATENCY: Histogram = _histogram(
    "jarvis_order_submission_seconds",
    "Wall-clock duration of one order dispatch, by route (market or pending).",
    ("route",),
)

# ── MT5 market data ─────────────────────────────────────────────────────────
#: `source` is what the frame actually is: `live_mt5`, or `synthetic` when the
#: terminal could not answer. A non-zero synthetic rate on a live account is the
#: single most important number on this page — it means decisions are being made
#: on bars no broker quoted.
MT5_RATES_FETCHES: Counter = _counter(
    "jarvis_mt5_rates_fetches_total",
    "Market-data fetches, by timeframe and by the provenance of the frame returned.",
    ("timeframe", "source"),
    max_series=256,
)

MT5_RATES_LATENCY: Histogram = _histogram(
    "jarvis_mt5_rates_fetch_seconds",
    "Wall-clock duration of one market-data fetch.",
    ("timeframe",),
)

# ── Broker lock ─────────────────────────────────────────────────────────────
#: `result` is `acquired` or `busy`. `busy` means a broker call never returned
#: and Python could not interrupt it — the platform is degraded, not slow.
BROKER_LOCK_ACQUISITIONS: Counter = _counter(
    "jarvis_broker_lock_acquisitions_total",
    "Attempts to take the process-wide broker lock, by result.",
    ("result",),
)

BROKER_LOCK_WAIT: Histogram = _histogram(
    "jarvis_broker_lock_wait_seconds",
    "Time spent waiting to take the broker lock.",
)

# ── News ────────────────────────────────────────────────────────────────────
#: `outcome` is `ok`, `empty`, `rate_limited`, `blocked`, `error` or `skipped`
#: (we are still inside a backoff window, so no request was made at all).
NEWS_FETCHES: Counter = _counter(
    "jarvis_news_source_fetches_total",
    "News source fetches, by source and outcome.",
    ("source", "outcome"),
)

NEWS_FETCH_LATENCY: Histogram = _histogram(
    "jarvis_news_source_fetch_seconds",
    "Wall-clock duration of one news source fetch.",
    ("source",),
)

#: What the calendar handed to callers actually was. `synthetic` is the
#: fabricated deterministic calendar — it drives a hard MACRO gate, so a non-zero
#: rate here means a gate is being decided by data no feed produced.
NEWS_CALENDAR_SERVED: Counter = _counter(
    "jarvis_news_calendar_served_total",
    "News calendars served to callers, by provenance.",
    ("origin",),
)

# ── HTTP ────────────────────────────────────────────────────────────────────
#: `route` is normalised before it is used as a label (see
#: `jarvis.api.server._normalize_route`); an arbitrary path from a hostile
#: client must not be able to mint unbounded series.
HTTP_REQUESTS: Counter = _counter(
    "jarvis_http_requests_total",
    "HTTP responses sent, by normalised route and status class.",
    ("route", "status_class"),
    max_series=256,
)

HTTP_LATENCY: Histogram = _histogram(
    "jarvis_http_request_seconds",
    "Wall-clock duration of one HTTP request, by normalised route.",
    ("route",),
    max_series=256,
)

# ── Process / internals ─────────────────────────────────────────────────────
#: Depth of the in-process caches. The point is the *shape* of this number: a
#: cache keyed on caller-supplied input that only ever grows is a leak, and it
#: is invisible until it is measured.
CACHE_ENTRIES: Gauge = _gauge(
    "jarvis_cache_entries",
    "Live entries in a named in-process cache.",
    ("cache",),
)

#: Series collapsed into `other` because a metric hit its cardinality ceiling.
#: Non-zero means some label is being fed unvalidated input.
CARDINALITY_OVERFLOW: Gauge = _gauge(
    "jarvis_metrics_series_overflow",
    "1 when a metric has hit its series ceiling and started collapsing labels.",
    ("metric",),
)

__all__ = [
    "DECISION_EVALUATIONS", "DECISION_LATENCY",
    "ORDERS_SUBMITTED", "ORDER_LATENCY",
    "MT5_RATES_FETCHES", "MT5_RATES_LATENCY",
    "BROKER_LOCK_ACQUISITIONS", "BROKER_LOCK_WAIT",
    "NEWS_FETCHES", "NEWS_FETCH_LATENCY", "NEWS_CALENDAR_SERVED",
    "HTTP_REQUESTS", "HTTP_LATENCY",
    "CACHE_ENTRIES", "CARDINALITY_OVERFLOW",
]
