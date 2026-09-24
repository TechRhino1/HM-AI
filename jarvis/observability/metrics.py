"""Counters, gauges and timers that can be scraped.

WHY THIS EXISTS
---------------
The platform had no counters at all. Every question an operator actually asks
in production — is the broker answering, are orders being refused, is the news
calendar real or synthetic, how long does a decision take — had to be answered
by reading source code or by tailing logs. The one number that *was* available
(`/api/diagnostics`) is an account snapshot, i.e. not a health signal.

Design constraints, in order:

1. **Zero third-party dependency.** `prometheus_client` is not in
   `requirements.txt` and adding one for this is not worth the supply-chain
   surface. The exposition format is rendered by hand; it is ~30 lines of text.
2. **Instrumenting must never break the instrumented path.** A metric call is
   on the order path. Label/name mistakes raise loudly at *construction* time
   (a programmer error, caught by tests), but a runtime `observe()` only ever
   ignores a bad value.
3. **Bounded cardinality.** Labels are supplied by callers, and some of them
   (HTTP route, symbol) come from unvalidated input. A metric that grows one
   series per distinct input is a memory leak wearing a metrics costume, so
   every metric has a `max_series` ceiling and collapses overflow into a single
   `other` series instead of growing without limit.
4. **Thread-safe.** Instrumented paths run on request threads, scan pool
   threads and the orchestrator loop simultaneously.

This module is a leaf: it imports nothing from `jarvis.*`.
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

#: Per-metric ceiling on distinct label combinations. Chosen well above any
#: legitimate cardinality (a symbol universe is tens, not hundreds) and well
#: below what a hostile or merely careless caller could use to grow memory.
DEFAULT_MAX_SERIES = 512

#: The series that absorbs everything past `max_series`. Visible in the
#: exposition output on purpose: silent truncation would make the metric lie.
OVERFLOW_LABEL = "other"

DEFAULT_BUCKETS: Tuple[float, ...] = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
)


class MetricsError(ValueError):
    """A metric was declared wrongly. Raised at construction, never at observe."""


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise MetricsError(
            f"metric name {name!r} must match [a-zA-Z_:][a-zA-Z0-9_:]* "
            f"(Prometheus exposition format)"
        )
    return name


def _check_labels(label_names: Sequence[str]) -> Tuple[str, ...]:
    for label in label_names:
        if not isinstance(label, str) or not _LABEL_RE.match(label):
            raise MetricsError(f"label name {label!r} must match [a-zA-Z_][a-zA-Z0-9_]*")
    if len(set(label_names)) != len(label_names):
        raise MetricsError(f"duplicate label names in {tuple(label_names)!r}")
    return tuple(label_names)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    return as_float if math.isfinite(as_float) else None


class _Metric:
    """Shared machinery: label resolution, series storage, cardinality ceiling."""

    __slots__ = ("name", "help", "metric_type", "label_names", "max_series",
                 "_lock", "_series", "_overflowed")

    def __init__(self, name: str, help: str, label_names: Sequence[str] = (),
                 max_series: int = DEFAULT_MAX_SERIES) -> None:
        self.name = _check_name(name)
        self.help = help or ""
        self.metric_type = "untyped"
        self.label_names = _check_labels(label_names)
        self.max_series = int(max_series)
        self._lock = threading.Lock()
        self._series: Dict[Tuple[str, ...], Any] = {}
        self._overflowed = False

    # ── Series management ───────────────────────────────────────────────────
    def _resolve(self, supplied: Dict[str, Any]) -> Tuple[str, ...]:
        """Label dict -> positional tuple. Unknown/missing labels are errors.

        These raise because they are always a typo in the calling code, and a
        metric that silently dropped a label would report a different series
        than the one the author meant — indistinguishable from correct data.
        """
        unknown = set(supplied) - set(self.label_names)
        if unknown:
            raise MetricsError(
                f"{self.name}: unknown label(s) {sorted(unknown)}; "
                f"declared labels are {list(self.label_names)}"
            )
        missing = [label for label in self.label_names if label not in supplied]
        if missing:
            raise MetricsError(f"{self.name}: missing label(s) {missing}")
        return tuple(str(supplied[label]) for label in self.label_names)

    def _slot(self, key: Tuple[str, ...], initial: Any) -> Any:
        """The storage for `key`, creating it if there is room."""
        with self._lock:
            slot = self._series.get(key)
            if slot is not None:
                return slot
            if self.label_names and len(self._series) >= self.max_series:
                # Ceiling reached. Collapse into one overflow series rather than
                # growing without bound; `labels()` renders it as
                # {every_label="other"} so it is obvious in a scrape.
                self._overflowed = True
                overflow = tuple(OVERFLOW_LABEL for _ in self.label_names)
                slot = self._series.get(overflow)
                if slot is None:
                    slot = initial()
                    self._series[overflow] = slot
                return slot
            slot = initial()
            self._series[key] = slot
            return slot

    def reset(self) -> None:
        """Zero every series. Keeps the declaration, so live references work."""
        with self._lock:
            self._series.clear()
            self._overflowed = False

    # ── Publication ─────────────────────────────────────────────────────────
    def samples(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def render(self) -> List[str]:
        lines = [f"# HELP {self.name} {_escape(self.help)}",
                 f"# TYPE {self.name} {self.metric_type}"]
        for sample in self.samples():
            labels = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sample["labels"].items())
            suffix = "{" + labels + "}" if labels else ""
            lines.append(f"{self.name}{suffix} {sample['value']!r}")
        return lines


class Counter(_Metric):
    """A monotonically increasing total. Use for "how many times did X happen"."""

    def __init__(self, name: str, help: str, label_names: Sequence[str] = (),
                 max_series: int = DEFAULT_MAX_SERIES) -> None:
        super().__init__(name, help, label_names, max_series)
        self.metric_type = "counter"

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        value = _finite_or_none(amount)
        if value is None or value < 0:
            return  # A counter can only go up; a NaN must not poison the series.
        slot = self._slot(self._resolve(labels), lambda: [0.0])
        with self._lock:
            slot[0] += value

    def samples(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"labels": dict(zip(self.label_names, key)), "value": value[0]}
                    for key, value in self._series.items()]


class Gauge(_Metric):
    """A value that goes up and down: queue depth, cache size, lock age."""

    def __init__(self, name: str, help: str, label_names: Sequence[str] = (),
                 max_series: int = DEFAULT_MAX_SERIES) -> None:
        super().__init__(name, help, label_names, max_series)
        self.metric_type = "gauge"

    def set(self, value: float, **labels: Any) -> None:
        number = _finite_or_none(value)
        if number is None:
            return
        slot = self._slot(self._resolve(labels), lambda: [0.0])
        with self._lock:
            slot[0] = number

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        number = _finite_or_none(amount)
        if number is None:
            return
        slot = self._slot(self._resolve(labels), lambda: [0.0])
        with self._lock:
            slot[0] += number

    def dec(self, amount: float = 1.0, **labels: Any) -> None:
        self.inc(-amount, **labels)

    def samples(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"labels": dict(zip(self.label_names, key)), "value": value[0]}
                    for key, value in self._series.items()]


class Histogram(_Metric):
    """Observation durations in seconds, with cumulative buckets.

    Stores count, sum, min and max alongside the buckets so the useful
    statistics are available without a query language: `sum / count` is the mean
    and does not need the buckets to be trusted.
    """

    __slots__ = ("buckets",)

    def __init__(self, name: str, help: str, label_names: Sequence[str] = (),
                 buckets: Iterable[float] = DEFAULT_BUCKETS,
                 max_series: int = DEFAULT_MAX_SERIES) -> None:
        super().__init__(name, help, label_names, max_series)
        self.metric_type = "histogram"
        buckets = tuple(float(b) for b in buckets)
        if not buckets or buckets != tuple(sorted(buckets)):
            raise MetricsError("histogram buckets must be a non-empty ascending sequence")
        self.buckets = buckets

    def observe(self, seconds: float, **labels: Any) -> None:
        value = _finite_or_none(seconds)
        if value is None or value < 0:
            return
        width = len(self.buckets)
        slot = self._slot(self._resolve(labels), lambda: [0.0, 0.0, math.inf, 0.0] + [0.0] * width)
        with self._lock:
            slot[0] += 1.0                       # count
            slot[1] += value                     # sum
            if value < slot[2]:
                slot[2] = value                  # min
            if value > slot[3]:
                slot[3] = value                  # max
            # Only the bucket the observation falls into is incremented; the
            # exposition format wants CUMULATIVE counts, which `samples()`
            # derives. Incrementing every bucket the value is under as well
            # would double-count.
            for index, edge in enumerate(self.buckets):
                if value <= edge:
                    slot[4 + index] += 1.0
                    break

    def time(self, **labels: Any) -> "Timer":
        """A `Timer` bound to this histogram. Use as a context manager."""
        return Timer(self, **labels)

    def samples(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._lock:
            for key, slot in self._series.items():
                labels = dict(zip(self.label_names, key))
                count, total, minimum, maximum = slot[0], slot[1], slot[2], slot[3]
                cumulative = 0.0
                for index, edge in enumerate(self.buckets):
                    cumulative += slot[4 + index]
                    out.append({"labels": dict(labels, le=_format_number(edge)),
                                "value": cumulative, "suffix": "_bucket"})
                # Anything above the last declared edge has no bucket of its own;
                # the exposition format still owes the scraper a +Inf count.
                out.append({"labels": dict(labels, le="+Inf"),
                            "value": count, "suffix": "_bucket"})
                out.append({"labels": labels, "value": count, "suffix": "_count"})
                out.append({"labels": labels, "value": total, "suffix": "_sum"})
                out.append({"labels": labels, "value": minimum if count else 0.0,
                            "suffix": "_min"})
                out.append({"labels": labels, "value": maximum, "suffix": "_max"})
        return out

    def render(self) -> List[str]:
        lines = [f"# HELP {self.name} {_escape(self.help)}",
                 f"# TYPE {self.name} {self.metric_type}"]
        for sample in self.samples():
            labels = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sample["labels"].items())
            suffix = "{" + labels + "}" if labels else ""
            lines.append(f"{self.name}{sample.get('suffix', '')}{suffix} {sample['value']!r}")
        return lines


def _format_number(value: float) -> str:
    if value == math.inf:
        return "+Inf"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


class Timer:
    """Wall-clock duration of a block, recorded into a :class:`Histogram`.

    The elapsed time is also readable from the object, because the same
    measurement usually belongs in the log line as `duration_ms` and publishing
    it twice from two clocks would let them disagree.
    """

    __slots__ = ("_histogram", "_labels", "_start", "elapsed_sec")

    def __init__(self, histogram: Histogram, **labels: Any) -> None:
        self._histogram = histogram
        self._labels = labels
        self._start = time.monotonic()
        self.elapsed_sec = 0.0

    def __enter__(self) -> "Timer":
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        self.stop()
        return False

    def stop(self) -> float:
        """Record and return the elapsed seconds. Idempotent."""
        if not self.elapsed_sec:
            self.elapsed_sec = time.monotonic() - self._start
            self._histogram.observe(self.elapsed_sec, **self._labels)
        return self.elapsed_sec

    @property
    def elapsed_ms(self) -> float:
        return round(self.elapsed_sec * 1000.0, 3)


class Registry:
    """The set of metrics the process publishes.

    `counter` / `gauge` / `histogram` are get-or-create, so a module that is
    imported twice (or imported and reloaded) cannot end up publishing two
    metrics under one name — which is what would make a scrape ambiguous.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: Dict[str, _Metric] = {}
        self._started_monotonic = time.monotonic()

    def register(self, metric: _Metric) -> _Metric:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                if existing is metric:
                    return existing
                if type(existing) is not type(metric):
                    raise MetricsError(
                        f"{metric.name} is already registered as a "
                        f"{existing.metric_type}, not a {metric.metric_type}"
                    )
                if existing.label_names != metric.label_names:
                    raise MetricsError(
                        f"{metric.name} is already registered with labels "
                        f"{list(existing.label_names)}, not {list(metric.label_names)}"
                    )
                return existing
            self._metrics[metric.name] = metric
            return metric

    def counter(self, name: str, help: str, label_names: Sequence[str] = (),
                **kwargs: Any) -> Counter:
        return self.register(Counter(name, help, label_names, **kwargs))  # type: ignore[return-value]

    def gauge(self, name: str, help: str, label_names: Sequence[str] = (),
              **kwargs: Any) -> Gauge:
        return self.register(Gauge(name, help, label_names, **kwargs))  # type: ignore[return-value]

    def histogram(self, name: str, help: str, label_names: Sequence[str] = (),
                  **kwargs: Any) -> Histogram:
        return self.register(Histogram(name, help, label_names, **kwargs))  # type: ignore[return-value]

    def get(self, name: str) -> Optional[_Metric]:
        with self._lock:
            return self._metrics.get(name)

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._metrics)

    def reset_all(self) -> None:
        """Zero every registered metric without unregistering it.

        Tests need this: metrics are module-level singletons, so a value left by
        one test is visible to the next. Removing the metrics would not help —
        the instrumented modules hold their own references.
        """
        with self._lock:
            for metric in self._metrics.values():
                metric.reset()

    def uptime_seconds(self) -> float:
        """Seconds since this registry was created — i.e. since import.

        Published rather than derived so a caller does not have to reach into
        the registry's internals to get the one number it owns.
        """
        return round(time.monotonic() - self._started_monotonic, 3)

    def collect(self) -> List[Dict[str, Any]]:
        """Every sample, as plain dicts. The JSON shape for `/api/metrics`."""
        out: List[Dict[str, Any]] = []
        with self._lock:
            metrics = list(self._metrics.values())
        for metric in metrics:
            for sample in metric.samples():
                out.append({
                    "name": metric.name + sample.get("suffix", ""),
                    "type": metric.metric_type,
                    "labels": sample["labels"],
                    "value": sample["value"],
                })
        out.append({
            "name": "jarvis_process_uptime_seconds",
            "type": "gauge",
            "labels": {},
            "value": self.uptime_seconds(),
        })
        return out

    def render_prometheus(self) -> str:
        """Prometheus text exposition format, ready to be scraped."""
        with self._lock:
            metrics = list(self._metrics.values())
        blocks = [metric.render() for metric in metrics]
        blocks.append([
            "# HELP jarvis_process_uptime_seconds Seconds since this process started.",
            "# TYPE jarvis_process_uptime_seconds gauge",
            f"jarvis_process_uptime_seconds {self.uptime_seconds()!r}",
        ])
        return "\n".join("\n".join(block) for block in blocks) + "\n"


#: The process-wide registry. Instrumented modules import metrics from
#: `jarvis.observability.instruments`, which builds on this.
REGISTRY = Registry()

counter = REGISTRY.counter
gauge = REGISTRY.gauge
histogram = REGISTRY.histogram
