"""The ``/health`` and ``/ready`` probes must be honest and must never 500.

Why this exists
---------------
Commit 35f13bf added a liveness/readiness endpoint (``_send_health``) for the
live terminal. A monitor is only as useful as the probe it reads, so the
invariants this file pins are:

* the probe is reachable **without a session** — a monitor has no credential;
* a fully healthy process answers ``200`` with the four documented keys
  (``broker_lock``, ``guard``, ``db``, ``ts``) and ``status == "ok"``;
* a failing dependency degrades the answer to ``503`` — it must NOT raise and
  turn into a ``500``, because a probe that 500s tells a monitor nothing;
* the three dependencies are probed **independently**: one failing must not
  hide the state of the other two.

The handler is driven directly rather than over a socket: the transport methods
are the only thing stubbed, so the real dispatch (``do_GET``) and the real
``_send_health`` / ``_send_json`` bodies run.
"""

from __future__ import annotations

import io
import json
import sqlite3

import pytest

from jarvis.api import server as server_module
from jarvis.common.timeout_guard import TimeoutGuard


class _StubBroker:
    """Only the surface ``_send_health`` touches."""

    def __init__(self, health=None, raises=None):
        self._health = health if health is not None else {"held": False, "age_sec": 0.0}
        self._raises = raises

    def broker_lock_health(self):
        if self._raises is not None:
            raise self._raises
        return self._health


class _RecordingHandler(server_module.JarvisRequestHandler):
    """A handler with the HTTP transport stubbed out, so nothing binds a socket.

    ``send_response`` / ``send_header`` / ``end_headers`` / ``wfile`` are the
    only seams replaced; every real method under test (``do_GET``,
    ``_send_health``, ``_send_json``) runs unchanged.
    """

    def __init__(self):
        self.headers = {}
        self.wfile = io.BytesIO()
        # A public address: the loopback admin bypass in `_is_local_request`
        # must not be able to make an unauthenticated request look authorised.
        self.client_address = ("203.0.113.9", 55555)
        self.path = "/"
        self.status_codes = []
        self.sent_headers = []
        self.mt5_client = _StubBroker()

    # ── transport stubs ────────────────────────────────────────────────────
    def send_response(self, code, message=None):
        self.status_codes.append(code)

    def send_header(self, keyword, value):
        self.sent_headers.append((keyword, value))

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self.status_codes.append(code)

    def log_message(self, *args, **kwargs):   # keep the suite output quiet
        pass

    # ── helpers ────────────────────────────────────────────────────────────
    @property
    def last_status(self):
        return self.status_codes[-1] if self.status_codes else None

    def body(self):
        raw = self.wfile.getvalue()
        return json.loads(raw.decode("utf-8")) if raw else None


class _FakeConnection:
    """A sqlite3 connection that answers the single ``SELECT 1`` probe."""

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        pass


def _healthy_handler():
    handler = _RecordingHandler()
    handler.mt5_client = _StubBroker()
    return handler


# ── healthy path ────────────────────────────────────────────────────────────
def test_a_healthy_process_answers_200_with_every_documented_key(monkeypatch):
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())
    # Deterministic guard state: an un-wedged pool.
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 0, "max_workers": 16,
                                 "available": 16, "wedged": False}),
    )
    handler = _healthy_handler()
    handler._send_health()

    assert handler.last_status == 200
    body = handler.body()
    assert body["status"] == "ok"
    for key in ("broker_lock", "guard", "db", "ts"):
        assert key in body, f"health body is missing '{key}'"
    assert body["db"] is True
    assert body["broker_lock"] == {"held": False, "age_sec": 0.0}
    assert isinstance(body["ts"], str) and body["ts"]


def test_a_stale_broker_lock_degrades_the_answer(monkeypatch):
    """The lock is the signal that a native broker call is wedged."""
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 0, "max_workers": 16,
                                 "available": 16, "wedged": False}),
    )
    handler = _RecordingHandler()
    handler.mt5_client = _StubBroker(health={"held": True, "age_sec": 30.0})
    handler._send_health()

    assert handler.last_status == 503
    assert handler.body()["status"] != "ok"
    assert handler.body()["broker_lock"]["held"] is True


def test_a_wedged_guard_degrades_the_answer(monkeypatch):
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 16, "max_workers": 16,
                                 "available": 0, "wedged": True}),
    )
    handler = _healthy_handler()
    handler._send_health()

    assert handler.last_status == 503
    assert handler.body()["status"] != "ok"
    assert handler.body()["guard"]["wedged"] is True


# ── failing DB probe: 503, never a 500 ──────────────────────────────────────
def test_a_raising_db_probe_answers_503_and_never_raises(monkeypatch):
    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(server_module.sqlite3, "connect", _boom)
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 0, "max_workers": 16,
                                 "available": 16, "wedged": False}),
    )
    handler = _healthy_handler()

    handler._send_health()          # must not raise

    assert handler.last_status == 503
    assert handler.last_status != 500
    assert handler.body()["status"] != "ok"
    assert handler.body()["db"] is False


# ── independence of the three probes ────────────────────────────────────────
def test_a_failing_db_does_not_hide_the_other_subsystems(monkeypatch):
    """Each probe is isolated, so one failure still reports the rest."""
    monkeypatch.setattr(
        server_module.sqlite3, "connect",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("no db")),
    )
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 0, "max_workers": 16,
                                 "available": 16, "wedged": False}),
    )
    handler = _healthy_handler()
    handler._send_health()

    body = handler.body()
    assert body["db"] is False
    # The other two are still reported, and they are real reports, not errors.
    assert body["guard"]["wedged"] is False
    assert "error" not in body["guard"]
    assert body["broker_lock"] == {"held": False, "age_sec": 0.0}


def test_a_raising_broker_probe_does_not_hide_the_other_subsystems(monkeypatch):
    """A broker-probe exception is named, and the other two are still reported."""
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 0, "max_workers": 16,
                                 "available": 16, "wedged": False}),
    )
    handler = _RecordingHandler()
    handler.mt5_client = _StubBroker(raises=RuntimeError("broker lock unavailable"))
    handler._send_health()

    body = handler.body()
    assert body["db"] is True
    assert "error" in body["broker_lock"]          # the failure is named
    assert body["guard"]["wedged"] is False
    assert "guard" in body and "db" in body


def test_an_unprobed_subsystem_degrades_the_status(monkeypatch):
    """A health endpoint must never answer "ok" about something it could not look at.

    ``_send_health`` isolates each probe and records an exception as
    ``{"error": ...}``. Reading that back with ``.get("held")`` / ``.get("wedged")``
    finds nothing, so both probes used to default to a healthy verdict and the
    endpoint answered ``200`` / ``"ok"`` even though it had not probed the broker
    lock or the guard at all — a monitor would have been lied to. Fixed: a probe
    that raised, or that returned no verdict key, now counts as degraded.
    """
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())

    def _guard_boom(cls):
        raise RuntimeError("guard unavailable")

    monkeypatch.setattr(TimeoutGuard, "health", classmethod(_guard_boom))
    handler = _RecordingHandler()
    handler.mt5_client = _StubBroker(raises=RuntimeError("broker lock unavailable"))
    handler._send_health()

    body = handler.body()
    # Both failures are reported…
    assert "error" in body["broker_lock"]
    assert "error" in body["guard"]
    assert body["db"] is True
    # …and the overall verdict degrades rather than claiming "ok".
    assert body["status"] != "ok"
    assert handler.last_status == 503


def test_a_probe_returning_no_verdict_key_also_degrades(monkeypatch):
    """Same failure mode without an exception: a dict with no `held`/`wedged` key.

    The real probes always emit those keys, so an empty dict means the caller got
    something unexpected — which must not be silently read as "healthy".
    """
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())
    monkeypatch.setattr(TimeoutGuard, "health", classmethod(lambda cls: {}))

    handler = _RecordingHandler()
    handler.mt5_client = _StubBroker(health={})
    handler._send_health()

    body = handler.body()
    assert body["status"] != "ok"
    assert handler.last_status == 503


# ── reachable without a session ─────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_the_probes_dispatch_without_authentication(monkeypatch, path):
    """A monitor holds no session, so ``/health`` and ``/ready`` must answer.

    The client address is public and no credential is supplied, and the token
    validator is forced to reject everything — so if the route were behind auth
    the response would be 401 rather than a probe result.
    """
    from jarvis.api.remote_auth import RemoteAuthEngine

    monkeypatch.setattr(
        server_module.JarvisRequestHandler, "start_background_syncer",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(RemoteAuthEngine, "validate_token",
                        classmethod(lambda cls, token: None))
    monkeypatch.setattr(server_module.sqlite3, "connect", lambda *a, **k: _FakeConnection())
    monkeypatch.setattr(
        TimeoutGuard, "health",
        classmethod(lambda cls: {"stuck_workers": 0, "max_workers": 16,
                                 "available": 16, "wedged": False}),
    )

    handler = _healthy_handler()
    handler.path = path
    handler.do_GET()

    assert handler.last_status == 200, f"{path} was not reachable without a session"
    assert handler.last_status != 401
    assert handler.body()["status"] == "ok"
