# Track 3 — Architecture Hardening & Observability

**Date:** 2026-09-24

---

## 1. Observability Module (`jarvis/observability/`)

A new 1,023-line module providing structured logging and scrapable metrics with **zero third-party dependencies**.

### Design

| Principle | Implementation |
|---|---|
| Zero deps | Prometheus text format rendered by hand (~30 lines); no `prometheus_client` |
| Never break the instrumented path | Runtime `observe()` silently ignores bad values; construction raises loudly (programmer error, caught by tests) |
| Bounded cardinality | `max_series` ceiling (default 512); overflow collapses into `_other` label |
| Thread-safe | All metrics use `threading.Lock`; registry uses its own lock |
| Leaf module | Imports nothing from `jarvis.*` — no import cycles |

### Components

| File | Lines | Purpose |
|---|---|---|
| `__init__.py` | 72 | Public API exports |
| `context.py` | 132 | Per-request context binding (`bind`, `bound`, `bound_scope`, `new_request_id`) |
| `logging_setup.py` | 206 | `JsonFormatter` (one JSON object per line) and `KeyValueFormatter` (human-readable terminal) |
| `metrics.py` | 454 | `Counter`, `Gauge`, `Histogram`, `Timer`, `Registry` with Prometheus exposition |
| `instruments.py` | 159 | Catalogue of all platform metrics (see below) |

### Instrumented Paths

| Module | Metrics | Log Events |
|---|---|---|
| `jarvis/api/server.py` | `HTTP_REQUESTS`, `HTTP_LATENCY` | Request start/end |
| `jarvis/application/orchestrator.py` | `DECISION_EVALUATIONS`, `DECISION_LATENCY` | Decision outcomes |
| `jarvis/execution/broker_lock.py` | `BROKER_LOCK_ACQUISITIONS`, `BROKER_LOCK_WAIT` | Lock contention |
| `jarvis/execution/execution_engine.py` | `ORDERS_SUBMITTED`, `ORDER_LATENCY` | Order dispatch results |
| `jarvis/market/data_feed.py` | `MT5_RATES_FETCHES`, `MT5_RATES_LATENCY`, `CACHE_ENTRIES` | Frame provenance |
| `jarvis/market/news.py` | `NEWS_FETCHES`, `NEWS_LATENCY`, `NEWS_SYNTHETIC_EVENTS` | Feed health |

### API Endpoint

`server.py` exposes `/api/metrics` returning `REGISTRY.collect()` as JSON, and a Prometheus-compatible text endpoint.

---

## 2. Security Audit

| Risk | Finding | Severity | Status |
|---|---|---|---|
| Hardcoded secrets | `DEFAULT_PASS_RAW` calls `_resolve_admin_password()` (dynamic, not hardcoded) | Info | OK |
| SQL injection | One f-string in `realtime_optimizer.py:32`, but values are parameterized (`params` passed to `execute()`) | Low | Safe |
| Unsafe deserialization | No `pickle.load`/`eval`/`exec` in production code | Info | OK |
| Swallowed exceptions | One `except Exception` in `position_monitor.py:1085` — **already fixed**: logs with `logger.debug(...)` instead of silent `pass`. Comment documents the previous typo that was hidden by silence. | Low | Fixed |
| Path traversal | API routes validate paths; no user-supplied paths used for file access | Info | OK |
| XML DoS | No XML parsing in production code | Info | OK |

---

## 3. Robustness & Reliability

| Area | Finding | Status |
|---|---|---|
| Timeouts on external calls | News fetch has `RATE_LIMIT_BACKOFF_SEC=600` and `BLOCKED_SOURCE_BACKOFF_SEC=3600` (§O fix). MT5 socket timeouts exist. | OK |
| Thread pool shutdown | `ParallelAnalystCluster` uses `ThreadPoolExecutor` but does not call `shutdown()` — potential leak on restart. | **Flagged** |
| Unbounded caches | News cache has 90s TTL; scan cache is file-based with explicit paths. No in-memory unbounded dicts found. | OK |
| Restart safety | State stored in SQLite (`jarvis_history.db`, `jarvis_circuit_state.db`) with WAL mode. Backup uses `sqlite3.Connection.backup()`. | OK |
| Single points of failure | MT5 terminal is a hard dependency; fallback to synthetic bars exists but is now labelled (`is_fallback`). | Documented |

---

## 4. Scalability

| Area | Finding | Status |
|---|---|---|
| Per-request blocking | Decision evaluation is synchronous; the 2.0s analyst timeout prevents indefinite hangs. | OK |
| Lock contention | Broker lock uses `threading.Lock`; metrics registry uses its own lock. No contention observed. | OK |
| O(n²) scans | Scan cache replay is O(n) per candidate. No hot-path O(n²) found. | OK |
| Queue depth | No unbounded queues in the execution path. | OK |

---

## 5. Tests

```
tests/test_observability.py ........................  24 passed
```

Coverage:
- Counter, Gauge, Histogram construction and behaviour
- Registry get-or-create, type mismatch, render, collect, reset
- Thread safety (8 threads × 1000 increments = 8000 exact)
- Cardinality ceiling (overflow → `_other` label)
- Context binding, scope isolation, request ID uniqueness
- JSON and KeyValue log formatters

---

## 6. Deliberately Not Changed

| Item | Reason |
|---|---|
| `ThreadPoolExecutor.shutdown()` | The executor is a module-level singleton in `ParallelAnalystCluster`; shutting it down would break subsequent calls. A proper lifecycle hook would need orchestrator changes beyond this track's scope. |
| `ai_score` persistence | Changes the candidate-cache schema; needs coordination with §P's instrument (`audit_selectivity_edge.py`). |
| `critique_confidence` column | Same schema-coordination issue as above. |
| Adding third-party deps | Design constraint: zero additional dependencies for observability. |

---

## 7. One Actionable Finding

**Thread pool lifecycle.** `ParallelAnalystCluster._executor` is created in `__init__` and never shut down. On platform restart (e.g. after a code push), the old executor's threads may remain alive until the process exits. This is a slow leak, not an immediate failure. Fix: add a `shutdown()` method called from the orchestrator's teardown path.
