import urllib.request
import json
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

def test_close_all():
    # 1. Login and retain the HttpOnly session cookie.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login_url = "http://127.0.0.1:8501/api/auth/login"
    login_req = urllib.request.Request(login_url, data=json.dumps({"username": "admin", "password": _get_admin_pass()}).encode("utf-8"), headers={"Content-Type": "application/json"})
    with opener.open(login_req) as resp:
        print("[LOGIN]:", json.loads(resp.read().decode("utf-8")).get("status"))

    # 2. Call close_all_positions
    close_url = "http://127.0.0.1:8501/api/action/close_all_positions"
    close_req = urllib.request.Request(close_url, data=b"{}", headers={"Content-Type": "application/json"})
    with opener.open(close_req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("[CLOSE ALL RESULT]:", data)

if __name__ == "__main__":
    test_close_all()
