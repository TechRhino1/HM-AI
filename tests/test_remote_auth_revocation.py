"""Token revocation, bookkeeping pruning, and the ADMIN password floor.

Why this exists
---------------
Commit 35f13bf turned the revoked-token set into ``_RevokedTokens`` — a ``dict``
mapping token -> the moment the revocation may lapse — and gave
``validate_token`` two sweep steps: expired revocations, and failed-login
stamps older than one hour. It also raised the minimum new-password length to
**12 for the ADMIN account only**, keeping the historical 6-character floor for
non-admin users. The invariants this file protects:

* a revoked token cannot validate;
* a stale (already-expired) revocation entry is swept, so remembering it forever
  is not the cost of revoking;
* failed-login stamps older than an hour are swept, but ones inside the hour are
  kept (the window is 3600s, not the 300s rate-limit window);
* ``change_password`` refuses a sub-12-character ADMIN password, accepts 12+,
  and still accepts 6-11 characters for a NON-admin account.

``tests/test_remote_auth.py`` already covers the basic revocation round-trip;
this file deliberately does not edit it and re-declares its own fixture, because
every store on ``RemoteAuthEngine`` is process-wide mutable state.
"""

from __future__ import annotations

import time

import pytest

from jarvis.api import remote_auth as ra
from jarvis.api.remote_auth import RemoteAuthEngine

TEST_PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def isolated_engine():
    """Snapshot every class-level store and hand back a known non-admin user.

    ``_revoked_tokens`` is restored as the object it was (the sibling test file
    restores it as a plain ``set``); each test starts with a fresh
    ``_RevokedTokens`` so the expiry semantics are exercised, not a bare set.
    """
    saved = {
        "_tokens": dict(RemoteAuthEngine._tokens),
        "_revoked_tokens": RemoteAuthEngine._revoked_tokens,
        "_failed_attempts": {k: list(v) for k, v in RemoteAuthEngine._failed_attempts.items()},
        "_users": {k: dict(v) for k, v in RemoteAuthEngine._users.items()},
        "_token_ttl": RemoteAuthEngine._token_ttl,
    }
    RemoteAuthEngine._revoked_tokens = ra._RevokedTokens()
    RemoteAuthEngine._tokens = {}
    RemoteAuthEngine._failed_attempts = {}
    RemoteAuthEngine._users["tester"] = {
        "username": "tester", "role": "TRADER", "full_name": "Test Account",
        "salt": "testsalt", "password_hash": RemoteAuthEngine._hash_password(TEST_PASS),
        "created_at": 0.0,
    }
    yield
    for key, value in saved.items():
        setattr(RemoteAuthEngine, key, value)


# ── revocation ──────────────────────────────────────────────────────────────
def test_a_revoked_token_is_rejected():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    assert RemoteAuthEngine.validate_token(token) is not None

    assert RemoteAuthEngine.revoke_token(token) is True
    assert RemoteAuthEngine.validate_token(token) is None


def test_a_revocation_is_recorded_with_an_expiry():
    """The new dict shape: a revocation carries the moment it may be forgotten."""
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine.revoke_token(token)

    revoked = RemoteAuthEngine._revoked_tokens
    assert token in revoked
    assert revoked[token] > time.time()
    assert revoked[token] <= time.time() + RemoteAuthEngine._token_ttl + 1


def test_an_expired_revocation_is_swept_and_stops_rejecting():
    """Pruning works: once the entry is stale it is dropped, and the token (still
    on the allow-list) validates again."""
    stale_token = RemoteAuthEngine.create_session_token("tester")["token"]
    live_token = RemoteAuthEngine.create_session_token("tester")["token"]
    assert stale_token != live_token

    # Craft an already-expired revocation for the first token, leaving it on the
    # allow-list so only the revocation stands between it and validation.
    RemoteAuthEngine._revoked_tokens[stale_token] = time.time() - 1.0
    assert stale_token in RemoteAuthEngine._tokens

    # Validating any other token runs the sweep.
    assert RemoteAuthEngine.validate_token(live_token) is not None
    assert stale_token not in RemoteAuthEngine._revoked_tokens, "stale revocation was not swept"

    assert RemoteAuthEngine.validate_token(stale_token) is not None


def test_a_stale_revocation_still_refuses_until_a_sweep_runs():
    """Documented ordering: the membership check precedes the sweep.

    ``validate_token`` returns early when the token is in ``_revoked_tokens``,
    and the sweep only runs afterwards — so a token whose own revocation entry
    has already lapsed is still refused on the first call, and is only freed
    once some other validation triggers a sweep. In practice the token's
    allow-list entry is removed by ``revoke_token``, so this cannot un-revoke a
    live session; the test pins the ordering rather than endorsing it.
    """
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine._revoked_tokens[token] = time.time() - 1.0

    assert RemoteAuthEngine.validate_token(token) is None     # still refused
    assert token in RemoteAuthEngine._revoked_tokens          # not yet swept


# ── failed-attempt pruning ──────────────────────────────────────────────────
def test_failed_attempts_older_than_an_hour_are_pruned():
    now = time.time()
    RemoteAuthEngine._failed_attempts["stale"] = [now - 4000.0]        # > 1h
    RemoteAuthEngine._failed_attempts["fresh"] = [now - 10.0]          # recent
    # Older than the 300s rate-limit window but younger than the 1h sweep window:
    # proves the sweep uses 3600s, not 300s.
    RemoteAuthEngine._failed_attempts["borderline"] = [now - 500.0]

    token = RemoteAuthEngine.create_session_token("tester")["token"]
    assert RemoteAuthEngine.validate_token(token) is not None

    attempts = RemoteAuthEngine._failed_attempts
    assert "stale" not in attempts, "an entry older than one hour was not pruned"
    assert "fresh" in attempts
    assert "borderline" in attempts, "the sweep window is narrower than one hour"


def test_a_fully_stale_key_is_removed_not_left_empty():
    RemoteAuthEngine._failed_attempts["only_stale"] = [time.time() - 7200.0]
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine.validate_token(token)
    assert "only_stale" not in RemoteAuthEngine._failed_attempts


# ── ADMIN password floor ────────────────────────────────────────────────────
def _admin_old_password() -> str:
    return ra._resolve_admin_password()


def test_an_admin_password_shorter_than_twelve_is_rejected():
    ok, msg = RemoteAuthEngine.change_password(
        ra.ADMIN_USERNAME, _admin_old_password(), "elevenchars"   # 11
    )
    assert ok is False
    assert "at least 12" in msg.lower()


def test_an_admin_password_of_exactly_twelve_is_accepted():
    new_pass = "twelvechars1"     # exactly 12
    ok, msg = RemoteAuthEngine.change_password(
        ra.ADMIN_USERNAME, _admin_old_password(), new_pass
    )
    assert ok is True and "updated" in msg.lower()
    # Check the stored hash directly — calling verify_credentials() would re-run
    # _init_default_users(), which re-syncs the admin record from the file.
    stored = RemoteAuthEngine._users[ra.ADMIN_USERNAME.strip().lower()]["password_hash"]
    assert RemoteAuthEngine._verify_password(new_pass, stored) is True


@pytest.mark.parametrize("new_pass", ["sixchr", "eightch1", "elevenchars"])
def test_a_non_admin_password_between_six_and_eleven_is_still_accepted(new_pass):
    """The asymmetry is deliberate: the ADMIN floor moved, the others did not."""
    ok, msg = RemoteAuthEngine.change_password("tester", TEST_PASS, new_pass)
    assert ok is True and "updated" in msg.lower()
    assert RemoteAuthEngine.verify_credentials("tester", new_pass)[0] is not None


def test_a_non_admin_password_shorter_than_six_is_rejected():
    ok, msg = RemoteAuthEngine.change_password("tester", TEST_PASS, "abcde")   # 5
    assert ok is False
    assert "at least 6" in msg.lower()
    assert RemoteAuthEngine.verify_credentials("tester", TEST_PASS)[0] is not None


def test_the_admin_floor_does_not_apply_to_a_non_admin():
    """A 6-char password is refused for ADMIN and accepted for a TRADER."""
    short = "sixchr"
    admin_ok, _ = RemoteAuthEngine.change_password(
        ra.ADMIN_USERNAME, _admin_old_password(), short
    )
    user_ok, _ = RemoteAuthEngine.change_password("tester", TEST_PASS, short)
    assert admin_ok is False
    assert user_ok is True
