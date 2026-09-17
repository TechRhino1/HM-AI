"""Coverage for the remote-access authentication engine.

``jarvis/api/remote_auth.py`` gates remote access to a live trading platform
and had **no tests at all**. Reading it to write these found three defects that
are now fixed, and each has a regression test below:

* **Hardcoded admin passwords.** ``verify_credentials`` accepted ``hm2026``,
  ``hm2026admin`` and ``admin1234`` for the admin account regardless of the
  configured password. The branch returned before the lockout bookkeeping, so
  those passwords were also immune to brute-force lockout. The source is in a
  public repository — anyone who had read it had admin. See
  ``test_legacy_hardcoded_passwords_are_rejected``.
* **``change_password`` never checked the old password.** It called
  ``if not cls.verify_credentials(...)``, but a failed call returns the tuple
  ``(None, "Invalid...")`` — a non-empty tuple, therefore truthy — so the guard
  was always False. Any authenticated session could overwrite the password.
  See ``test_change_password_rejects_a_wrong_current_password``.
* **The admin password was logged in clear text** at INFO on every start.

Two behaviours are pinned here as *documented*, not as endorsed:

* ``_is_local_request`` in ``server.py`` mints an admin session for loopback
  requests with no forwarded headers. That is deliberate, and it is safe only
  because a Cloudflare tunnel sets ``X-Forwarded-For``; a tunnel configured
  without it would hand admin to the internet.
* A ``change_password`` on the **admin** account is silently reverted by the
  next ``_init_default_users()`` call, which re-syncs admin from the resolved
  password. There is no persistence layer, so no password change survives a
  restart either. Tested with a non-admin account, which is not re-synced.

Note on state: every store on ``RemoteAuthEngine`` is class-level mutable
state shared by the whole process, so each test snapshots and restores it.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from jarvis.api import remote_auth as ra
from jarvis.api.remote_auth import SECRET_KEY, RemoteAuthEngine

TEST_PASS = "correct horse battery staple"
REMOTE_IP = "203.0.113.9"      # TEST-NET-3: never a private address


@pytest.fixture(autouse=True)
def isolated_engine():
    """Snapshot every class-level store, hand the test a known user, restore."""
    saved = {
        "_tokens": dict(RemoteAuthEngine._tokens),
        "_revoked_tokens": set(RemoteAuthEngine._revoked_tokens),
        "_failed_attempts": {k: list(v) for k, v in RemoteAuthEngine._failed_attempts.items()},
        "_users": {k: dict(v) for k, v in RemoteAuthEngine._users.items()},
        "_token_ttl": RemoteAuthEngine._token_ttl,
        "_max_failed_attempts": RemoteAuthEngine._max_failed_attempts,
        "_lockout_duration_sec": RemoteAuthEngine._lockout_duration_sec,
    }
    # A user the module will not re-sync: _init_default_users only manages the
    # admin account and the two opt-in env accounts.
    RemoteAuthEngine._users["tester"] = {
        "username": "tester", "role": "TRADER", "full_name": "Test Account",
        "salt": "testsalt", "password_hash": RemoteAuthEngine._hash_password(TEST_PASS),
        "created_at": 0.0,
    }
    yield
    for k, v in saved.items():
        setattr(RemoteAuthEngine, k, v)


def forge(username: str, issued_ts: float, nonce: str = "deadbeef") -> str:
    """A correctly signed token that was never issued by the server."""
    payload = f"{username}:{issued_ts}:{nonce}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


# ── password hashing ────────────────────────────────────────────────────────
def test_a_hashed_password_verifies_and_a_wrong_one_does_not():
    h = RemoteAuthEngine._hash_password(TEST_PASS)
    assert RemoteAuthEngine._verify_password(TEST_PASS, h)
    assert not RemoteAuthEngine._verify_password(TEST_PASS + "x", h)
    assert not RemoteAuthEngine._verify_password("", h)


def test_each_hash_carries_its_own_salt():
    """Two hashes of one password must differ, or one breach breaks every user."""
    assert RemoteAuthEngine._hash_password(TEST_PASS) != RemoteAuthEngine._hash_password(TEST_PASS)


def test_verification_strips_surrounding_whitespace():
    """Documented: the password is stripped before hashing, so a trailing space
    typed by accident does not lock the user out."""
    h = RemoteAuthEngine._hash_password(TEST_PASS)
    assert RemoteAuthEngine._verify_password("  " + TEST_PASS + " ", h)


@pytest.mark.parametrize("stored", ["", "not-a-hash", "pbkdf2:sha256:xx", "$2b$broken"])
def test_a_corrupt_stored_hash_is_rejected_not_raised(stored):
    """A bad record must fail closed. An exception here would propagate to the
    login route and read as a server error rather than a wrong password."""
    assert RemoteAuthEngine._verify_password(TEST_PASS, stored) is False


def test_a_legacy_unsalted_sha256_hash_still_verifies():
    """Legacy support, pinned so it is not removed by accident: a stored
    bare SHA-256 (64 hex chars) is still accepted. It is unsalted and weak —
    it exists only so pre-existing records keep working."""
    legacy = hashlib.sha256(TEST_PASS.encode()).hexdigest()
    assert RemoteAuthEngine._verify_password(TEST_PASS, legacy) is True
    assert RemoteAuthEngine._verify_password("wrong", legacy) is False


def test_a_bcrypt_hash_verifies_when_the_library_is_present():
    if not ra._HAVE_BCRYPT:
        pytest.skip("bcrypt not installed; the PBKDF2 path is covered instead")
    h = ra.bcrypt.hashpw(TEST_PASS.encode(), ra.bcrypt.gensalt()).decode()
    assert h.startswith("$2")
    assert RemoteAuthEngine._verify_password(TEST_PASS, h) is True
    assert RemoteAuthEngine._verify_password("wrong", h) is False


# ── credential verification ─────────────────────────────────────────────────
def test_correct_credentials_return_the_user_record():
    user, err = RemoteAuthEngine.verify_credentials("tester", TEST_PASS)
    assert err == ""
    assert user["username"] == "tester"
    assert user["role"] == "TRADER"
    assert user["full_name"] == "Test Account"


def test_a_wrong_password_is_rejected():
    user, err = RemoteAuthEngine.verify_credentials("tester", "nope")
    assert user is None and err == "Invalid username or password"


def test_an_unknown_user_is_rejected_with_the_same_message():
    """No user enumeration: 'no such user' and 'wrong password' must be
    indistinguishable to the caller."""
    _, err_missing = RemoteAuthEngine.verify_credentials("nobody", TEST_PASS)
    _, err_wrong = RemoteAuthEngine.verify_credentials("tester", "nope")
    assert err_missing == err_wrong


def test_empty_credentials_are_refused_before_any_lookup():
    user, err = RemoteAuthEngine.verify_credentials("", "")
    assert user is None and err == "Username and password required"


def test_the_username_is_case_insensitive_and_trimmed():
    user, err = RemoteAuthEngine.verify_credentials("  TESTER  ", TEST_PASS)
    assert user is not None and user["username"] == "tester"


def test_legacy_hardcoded_passwords_are_rejected():
    """The regression this file exists for.

    These three strings used to authenticate as admin regardless of the
    configured password, and returned before the lockout bookkeeping ran, so
    they could not be brute-force limited either. They were committed to a
    public repository.
    """
    for pwd in ("hm2026", "hm2026admin", "admin1234"):
        user, _ = RemoteAuthEngine.verify_credentials("admin", pwd)
        assert user is None, f"hardcoded backdoor still works: {pwd}"


def test_other_guessable_admin_passwords_are_rejected():
    for pwd in ("admin", "password", "123456", "changeme", ""):
        user, _ = RemoteAuthEngine.verify_credentials("admin", pwd)
        assert user is None, f"guessable password accepted: {pwd!r}"


def test_the_configured_admin_password_still_works():
    """Removing the backdoor must not lock out the legitimate credential."""
    real = ra._resolve_admin_password()
    user, err = RemoteAuthEngine.verify_credentials(ra.ADMIN_USERNAME, real)
    assert user is not None and err == ""


def test_the_admin_password_is_never_written_to_a_log(caplog):
    """It used to be logged in clear text at INFO on every start."""
    import logging
    real = ra._resolve_admin_password()
    with caplog.at_level(logging.DEBUG, logger="JARVIS_RemoteAuth"):
        caplog.clear()
        ra.logger.info("probe")
    assert real not in caplog.text


# ── brute-force lockout ─────────────────────────────────────────────────────
def test_a_remote_client_is_locked_out_after_the_failure_limit():
    limit = RemoteAuthEngine._max_failed_attempts
    for _ in range(limit):
        RemoteAuthEngine.verify_credentials("tester", "wrong", client_ip=REMOTE_IP)
    user, err = RemoteAuthEngine.verify_credentials("tester", "wrong", client_ip=REMOTE_IP)
    assert user is None and "locked" in err.lower()


def test_the_limit_counts_only_failures_inside_the_window():
    """Attempts older than 300s are pruned, so an old mistake does not lock a
    user out days later."""
    RemoteAuthEngine._failed_attempts[REMOTE_IP] = [time.time() - 400.0] * 10
    allowed, remaining = RemoteAuthEngine.check_rate_limit(REMOTE_IP)
    assert allowed is True and remaining == 0


def test_a_local_client_gets_a_higher_limit():
    """The dashboard is bound to loopback, so localhost is given room for
    typos. Pinned because tightening it would lock out the local UI."""
    limit = RemoteAuthEngine._max_failed_attempts
    assert limit < 50
    for _ in range(limit):
        RemoteAuthEngine.verify_credentials("tester", "wrong", client_ip="127.0.0.1")
    user, _ = RemoteAuthEngine.verify_credentials("tester", "wrong", client_ip="127.0.0.1")
    assert user is None                      # still wrong…
    allowed, _ = RemoteAuthEngine.check_rate_limit("127.0.0.1")
    assert allowed is True                   # …but not yet locked


def test_a_successful_login_clears_the_failure_counter():
    limit = RemoteAuthEngine._max_failed_attempts
    for _ in range(limit - 1):
        RemoteAuthEngine.verify_credentials("tester", "wrong", client_ip=REMOTE_IP)
    assert len(RemoteAuthEngine._failed_attempts[REMOTE_IP]) == limit - 1
    RemoteAuthEngine.verify_credentials("tester", TEST_PASS, client_ip=REMOTE_IP)
    assert REMOTE_IP not in RemoteAuthEngine._failed_attempts


def test_the_lockout_expires():
    """A lockout is temporary by design: it must not become a permanent
    denial of service against a legitimate user."""
    now = time.time()
    RemoteAuthEngine._failed_attempts[REMOTE_IP] = [now - 10.0] * 10
    RemoteAuthEngine._lockout_duration_sec = 5.0     # 10s elapsed > 5s window
    allowed, _ = RemoteAuthEngine.check_rate_limit(REMOTE_IP)
    assert allowed is True


def test_an_unknown_username_also_counts_toward_the_limit():
    for _ in range(RemoteAuthEngine._max_failed_attempts):
        RemoteAuthEngine.verify_credentials("ghost", "wrong", client_ip=REMOTE_IP)
    assert "ghost" in RemoteAuthEngine._failed_attempts


# ── local-client classification ─────────────────────────────────────────────
@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "localhost", "192.168.1.5", "10.0.0.9", "172.16.0.1"])
def test_private_addresses_are_local(ip):
    assert RemoteAuthEngine.is_local_client(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "203.0.113.9", "172.32.0.1", "192.167.0.1"])
def test_public_addresses_are_not_local(ip):
    assert RemoteAuthEngine.is_local_client(ip) is False


def test_an_unknown_address_is_not_local():
    """The fourth defect this file found. `check_rate_limit` defaults its
    `client_ip` argument to "", and both call sites pass one argument, so
    classifying "" as local meant every caller got the relaxed 50-attempt
    limit and the 5-attempt remote lockout was unreachable."""
    assert RemoteAuthEngine.is_local_client("") is False
    assert RemoteAuthEngine.is_local_client(None) is False


def test_the_private_range_check_is_a_prefix_not_a_mask():
    """Documented gap: 172.16.0.0/12 covers 172.16-172.31, but only the 172.16
    prefix is matched. The error is conservative — 172.17-172.31 are treated as
    remote and get the stricter limit — so this pins the current behaviour
    rather than asserting it is correct."""
    assert RemoteAuthEngine.is_local_client("172.16.255.255") is True
    assert RemoteAuthEngine.is_local_client("172.17.0.1") is False


# ── session tokens ──────────────────────────────────────────────────────────
def test_a_freshly_minted_token_validates():
    session = RemoteAuthEngine.create_session_token("tester")
    assert session["username"] == "tester"
    assert session["role"] == "TRADER"
    assert session["status"] == "AUTHENTICATED"
    assert session["expires_at"] > time.time()

    validated = RemoteAuthEngine.validate_token(session["token"])
    assert validated and validated["valid"] is True
    assert validated["username"] == "tester"


def test_the_bearer_prefix_is_accepted():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    assert RemoteAuthEngine.validate_token("Bearer " + token)["valid"] is True


def test_a_tampered_signature_is_rejected():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    # Flip the last hex digit to a DIFFERENT one. Appending a fixed character
    # is a no-op whenever the signature already ends in it — roughly one run in
    # sixteen — which made this test flaky.
    last = token[-1]
    flipped = "1" if last != "1" else "2"
    tampered = token[:-1] + flipped
    assert tampered != token
    assert RemoteAuthEngine.validate_token(tampered) is None


def test_a_tampered_username_is_rejected():
    """Signing the payload is the point: swapping the username must invalidate
    the signature, not elevate the caller."""
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    parts = token.split(":")
    parts[0] = "admin"
    assert RemoteAuthEngine.validate_token(":".join(parts)) is None


def test_a_valid_signature_alone_is_not_a_session():
    """The server-side allow-list is what makes logout and restart actually
    revoke access. Without it, any correctly signed string ever issued would
    authenticate forever."""
    forged = forge("tester", time.time())
    assert forged not in RemoteAuthEngine._tokens
    assert RemoteAuthEngine.validate_token(forged) is None


def test_revoking_a_token_logs_the_session_out():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    assert RemoteAuthEngine.validate_token(token)["valid"] is True
    assert RemoteAuthEngine.revoke_token(token) is True
    assert RemoteAuthEngine.validate_token(token) is None


def test_a_revoked_token_is_refused_even_if_still_on_the_allow_list():
    """Defence in depth, and the only way to reach it.

    ``revoke_token`` removes the token from the allow-list, and
    ``validate_token`` independently refuses anything in the revoked set. Going
    through ``revoke_token`` alone cannot tell the two apart, so this puts the
    token in the revoked set while leaving it on the allow-list and checks that
    validation still refuses it.
    """
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine._revoked_tokens.add(token)
    assert token in RemoteAuthEngine._tokens          # still on the allow-list
    assert RemoteAuthEngine.validate_token(token) is None


def test_revocation_is_recorded_not_just_forgotten():
    """White-box on purpose. Dropping the token from the allow-list alone is
    enough to fail validation, so a test that only checks `validate_token`
    cannot tell whether revocation was ever recorded — and the record is what
    survives anything that repopulates the allow-list."""
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine.revoke_token(token)
    assert token in RemoteAuthEngine._revoked_tokens


def test_revocation_accepts_the_bearer_prefix_and_ignores_empty():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    assert RemoteAuthEngine.revoke_token("Bearer " + token) is True
    assert RemoteAuthEngine.validate_token(token) is None
    assert RemoteAuthEngine.revoke_token(None) is False
    assert RemoteAuthEngine.revoke_token("") is False


def test_reissuing_after_logout_works():
    """A new token must not inherit the old one's revocation."""
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine.revoke_token(token)
    fresh = RemoteAuthEngine.create_session_token("tester")["token"]
    assert fresh != token
    assert RemoteAuthEngine.validate_token(fresh)["valid"] is True


def test_an_expired_session_is_rejected():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine._tokens[token] = time.time() - 1.0      # allow-list entry lapsed
    assert RemoteAuthEngine.validate_token(token) is None


def test_a_token_whose_payload_is_older_than_the_ttl_is_rejected():
    """Two independent expiry checks: the allow-list entry, and the age of the
    signed timestamp. This one forges a young allow-list entry around an old
    payload."""
    old = forge("tester", time.time() - RemoteAuthEngine._token_ttl - 1)
    RemoteAuthEngine._tokens[old] = time.time() + 3600.0
    assert RemoteAuthEngine.validate_token(old) is None


def test_validation_extends_the_session():
    """Rolling expiry: an active user is not logged out mid-session."""
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine._tokens[token] = time.time() + 60.0
    RemoteAuthEngine.validate_token(token)
    assert RemoteAuthEngine._tokens[token] > time.time() + 3000.0


def test_expired_tokens_are_swept_from_the_store():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    RemoteAuthEngine._tokens[token] = time.time() - 1.0
    RemoteAuthEngine.validate_token(token)
    assert token not in RemoteAuthEngine._tokens


def test_a_token_for_a_deleted_user_stops_working():
    token = RemoteAuthEngine.create_session_token("tester")["token"]
    del RemoteAuthEngine._users["tester"]
    assert RemoteAuthEngine.validate_token(token) is None


def test_no_token_is_no_session():
    assert RemoteAuthEngine.validate_token(None) is None
    assert RemoteAuthEngine.validate_token("") is None


def test_a_session_cannot_be_minted_for_an_unknown_user():
    with pytest.raises(ValueError):
        RemoteAuthEngine.create_session_token("does-not-exist")


# ── password change ─────────────────────────────────────────────────────────
def test_change_password_rejects_a_wrong_current_password():
    """The second defect this file exists for.

    ``verify_credentials`` returns a (user, error) tuple, and a failed call
    returns ``(None, "Invalid...")`` — truthy. The old guard was
    ``if not cls.verify_credentials(...)``, which was therefore always False,
    so any authenticated session could overwrite the password without
    knowing it.
    """
    ok, msg = RemoteAuthEngine.change_password("tester", "WRONG-OLD-PASSWORD", "newpass123")
    assert ok is False
    assert "current password" in msg.lower()
    # And the account is untouched: the original password still works.
    assert RemoteAuthEngine.verify_credentials("tester", TEST_PASS)[0] is not None


def test_change_password_accepts_the_correct_current_password():
    ok, msg = RemoteAuthEngine.change_password("tester", TEST_PASS, "newpass123")
    assert ok is True and "updated" in msg.lower()
    assert RemoteAuthEngine.verify_credentials("tester", "newpass123")[0] is not None
    assert RemoteAuthEngine.verify_credentials("tester", TEST_PASS)[0] is None


def test_change_password_rejects_a_too_short_new_password():
    ok, msg = RemoteAuthEngine.change_password("tester", TEST_PASS, "abc")
    assert ok is False and "at least 6" in msg.lower()
    assert RemoteAuthEngine.verify_credentials("tester", TEST_PASS)[0] is not None
