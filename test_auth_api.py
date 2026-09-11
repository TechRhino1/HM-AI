"""
Automated Authentication Test Suite for HM AI 4.0
Tests Login, Logout, Session Verification, Password Validation, and Token Revocation.
"""
import urllib.request
import urllib.error
import json
import sys
import os
import http.cookiejar

def _get_admin_pass():
    p = os.environ.get("JARVIS_ADMIN_PASS")
    if p:
        return p
    try:
        with open(".jarvis_admin_pass", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "CHANGE_ME"

def _get_trader_pass():
    return os.environ.get("JARVIS_TRADER_PASS", "CHANGE_ME")

BASE_URL = "http://127.0.0.1:8501"
COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIE_JAR))

def run_auth_tests():
    print("=" * 80)
    print("RUNNING HM AI 4.0 AUTHENTICATION (LOGIN, LOGOUT, VERIFY & ROLES) TEST SUITE")
    print("=" * 80)
    passed = 0
    failed = 0

    def test_post(name, endpoint, payload, expected_status, headers=None, validator=None):
        nonlocal passed, failed
        url = f"{BASE_URL}{endpoint}"
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        req = urllib.request.Request(url, data=body, headers=h, method="POST")

        try:
            with OPENER.open(req) as resp:
                status = resp.status
                data = json.loads(resp.read().decode("utf-8"))
                if status == expected_status and (validator is None or validator(data)):
                    print(f"[PASS] {name:50} -> HTTP {status} OK | Response Validated")
                    passed += 1
                    return data
                else:
                    print(f"[FAIL] {name:50} -> Expected {expected_status}, got {status}")
                    failed += 1
                    return None
        except urllib.error.HTTPError as e:
            if e.code == expected_status:
                try:
                    data = json.loads(e.read().decode("utf-8"))
                except Exception:
                    data = {}
                if validator is None or validator(data):
                    print(f"[PASS] {name:50} -> HTTP {e.code} Expected | Error Handled Correctly")
                    passed += 1
                    return data
            print(f"[FAIL] {name:50} -> Expected HTTP {expected_status}, got {e.code}")
            failed += 1
            return None
        except Exception as ex:
            print(f"[FAIL] {name:50} -> Exception: {ex}")
            failed += 1
            return None

    # 1. Test Login with Valid Admin Credentials
    admin_session = test_post(
        "1. Login Valid Admin (admin / configured)",
        "/api/auth/login",
         {"username": "admin", "password": _get_admin_pass()},
        200,
        validator=lambda d: d.get("status") == "AUTHENTICATED" and d.get("role") == "ADMIN"
    )

    # 2. Test Login with Invalid Password (Rejection)
    test_post(
        "2. Login Invalid Password (admin / wrongpass123)",
        "/api/auth/login",
        {"username": "admin", "password": "wrongpass123"},
        401,
        validator=lambda d: d.get("status") == "UNAUTHORIZED"
    )

    # 3. Test Login with Invalid Username
    test_post(
        "3. Login Unknown User (unknown_user / 123456)",
        "/api/auth/login",
        {"username": "unknown_user", "password": "123456"},
        401,
        validator=lambda d: d.get("status") == "UNAUTHORIZED"
    )

    # 4. Verify the HttpOnly-cookie admin session.
    test_post(
        "4. Verify Valid Admin Cookie Session",
        "/api/auth/verify",
        {},
        200,
        validator=lambda d: d.get("valid") is True and d.get("user", {}).get("username") == "admin"
    )

    # 5. Test Logout (server-side session revocation).
    test_post(
        "5. Logout Admin Session (/api/auth/logout)",
        "/api/auth/logout",
        {},
        200,
        validator=lambda d: d.get("status") == "LOGGED_OUT"
    )

    # 6. Test Verification of Revoked Cookie Session.
    test_post(
        "6. Verify Revoked Cookie Session (Must be Rejected)",
        "/api/auth/verify",
        {},
        401,
        validator=lambda d: d.get("valid") is False
    )

    print("=" * 80)
    print(f"TEST RESULTS: {passed} PASSED | {failed} FAILED")
    print("=" * 80)

    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run_auth_tests()
