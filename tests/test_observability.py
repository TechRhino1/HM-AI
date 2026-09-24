"""Tests for jarvis.observability — metrics, context, and logging.

The module is deliberately a leaf (no imports from jarvis.*) so these tests
run fast and need no fixtures.
"""
import json
import logging
import threading

import pytest

from jarvis.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsError,
    Registry,
    DEFAULT_MAX_SERIES,
    OVERFLOW_LABEL,
)
from jarvis.observability.context import (
    bind,
    bound,
    bound_scope,
    clear,
    current_request_id,
    new_request_id,
    unbind,
)
from jarvis.observability.logging_setup import JsonFormatter, KeyValueFormatter


# --------------------------------------------------------------------------
# Metrics — construction validation
# --------------------------------------------------------------------------

def test_counter_rejects_bad_name():
    with pytest.raises(MetricsError):
        Counter("123_bad", "help")


def test_gauge_rejects_duplicate_labels():
    with pytest.raises(MetricsError):
        Gauge("x", "help", ("a", "a"))


def test_histogram_rejects_unsorted_buckets():
    with pytest.raises(MetricsError):
        Histogram("x", "help", buckets=(10.0, 1.0))


# --------------------------------------------------------------------------
# Counter
# --------------------------------------------------------------------------

def test_counter_starts_empty():
    c = Counter("c", "help")
    # No series until first observation (lazy allocation)
    assert c.samples() == []


def test_counter_increments():
    c = Counter("c", "help")
    c.inc()
    c.inc(5)
    assert c.samples()[0]["value"] == 6.0


def test_counter_ignores_negative():
    c = Counter("c", "help")
    c.inc(3)
    c.inc(-1)
    assert c.samples()[0]["value"] == 3.0


def test_counter_with_labels():
    c = Counter("c", "help", ("status",))
    c.inc(1, status="ok")
    c.inc(2, status="err")
    samples = {frozenset(s["labels"].items()): s["value"] for s in c.samples()}
    assert samples[frozenset({("status", "ok")})] == 1.0
    assert samples[frozenset({("status", "err")})] == 2.0


# --------------------------------------------------------------------------
# Gauge
# --------------------------------------------------------------------------

def test_gauge_set_inc_dec():
    g = Gauge("g", "help")
    g.set(10)
    g.inc(3)
    g.dec(2)
    assert g.samples()[0]["value"] == 11.0


def test_gauge_negative_is_allowed():
    g = Gauge("g", "help")
    g.set(-5)
    assert g.samples()[0]["value"] == -5.0


# --------------------------------------------------------------------------
# Histogram
# --------------------------------------------------------------------------

def test_histogram_buckets():
    h = Histogram("h", "help", buckets=(1.0, 10.0, 100.0))
    h.observe(5)
    h.observe(50)
    samples = {s["labels"].get("le", s.get("suffix", "sum")): s["value"]
               for s in h.samples()}
    assert samples["1"] == 0.0
    assert samples["10"] == 1.0
    assert samples["100"] == 2.0
    assert samples["+Inf"] == 2.0
    assert samples.get("_count") == 2.0


def test_histogram_min_max():
    h = Histogram("h", "help", buckets=(1.0, 10.0))
    h.observe(3)
    h.observe(7)
    samples = {s.get("suffix", ""): s["value"] for s in h.samples()}
    assert samples.get("_min") == 3.0
    assert samples.get("_max") == 7.0


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

def test_registry_get_or_create():
    r = Registry()
    c1 = r.counter("x", "help")
    c2 = r.counter("x", "help")
    assert c1 is c2


def test_registry_rejects_type_mismatch():
    r = Registry()
    r.counter("x", "help")
    with pytest.raises(MetricsError):
        r.gauge("x", "help")


def test_registry_render_prometheus():
    r = Registry()
    c = r.counter("orders", "Total orders")
    c.inc(7)
    text = r.render_prometheus()
    assert "# TYPE orders counter" in text
    assert "orders 7.0" in text
    assert "jarvis_process_uptime_seconds" in text


def test_registry_collect_jsonable():
    r = Registry()
    c = r.counter("c", "help")
    c.inc(1)
    rows = r.collect()
    assert any(row["name"] == "c" and row["value"] == 1.0 for row in rows)
    assert any(row["name"] == "jarvis_process_uptime_seconds" for row in rows)


def test_registry_reset_all_clears_series():
    r = Registry()
    c = r.counter("c", "help")
    c.inc(5)
    r.reset_all()
    assert c.samples() == []


# --------------------------------------------------------------------------
# Thread safety — hammer a counter from many threads
# --------------------------------------------------------------------------

def test_counter_thread_safety():
    c = Counter("c", "help")
    errors = []

    def worker():
        try:
            for _ in range(1000):
                c.inc()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert c.samples()[0]["value"] == 8000.0


# --------------------------------------------------------------------------
# Cardinality ceiling
# --------------------------------------------------------------------------

def test_max_series_overflow():
    r = Registry()
    c = r.counter("c", "help", ("id",), max_series=3)
    for i in range(5):
        c.inc(1, id=str(i))
    samples = c.samples()
    labels = [tuple(sorted(s["labels"].items())) for s in samples]
    assert any(("id", OVERFLOW_LABEL) in lbl for lbl in labels)


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------

def test_bind_and_bound():
    clear()
    bind(symbol="XAUUSD", route="/api/trade")
    assert bound().get("symbol") == "XAUUSD"
    assert bound().get("route") == "/api/trade"
    assert bound().get("missing") is None


def test_bound_scope_isolated():
    clear()
    bind(symbol="A")
    with bound_scope(symbol="B", extra="x"):
        assert bound().get("symbol") == "B"
        assert bound().get("extra") == "x"
    assert bound().get("symbol") == "A"
    assert bound().get("extra") is None


def test_new_request_id_is_unique():
    ids = {new_request_id() for _ in range(100)}
    assert len(ids) == 100


def test_current_request_id_after_bind():
    clear()
    rid = new_request_id()
    bind(request_id=rid)
    assert current_request_id() == rid


# --------------------------------------------------------------------------
# Logging formatters
# --------------------------------------------------------------------------

def test_json_formatter_outputs_valid_json():
    fmt = JsonFormatter()
    record = logging.LogRecord(
        "test", logging.INFO, "", 0, "hello", (), None
    )
    out = fmt.format(record)
    parsed = json.loads(out)
    assert parsed["msg"] == "hello"
    assert parsed["level"] == "INFO"


def test_key_value_formatter_includes_bound_fields():
    clear()
    bind(request_id="abc123")
    fmt = KeyValueFormatter()
    record = logging.LogRecord(
        "test", logging.WARNING, "", 0, "alert", (), None
    )
    out = fmt.format(record)
    assert "alert" in out
    assert "request_id=abc123" in out
