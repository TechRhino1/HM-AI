"""Every response the dashboard serves must carry the hardened security headers.

Why this exists
---------------
``jarvis/api/server.py`` centralises the response headers in ``_SECURITY_HEADERS``
and applies them through ``_send_security_headers()`` at each send site:
``_send_json``, both branches of ``_serve_static_file`` and ``_serve_template``.
A header that is only present on the constant is worth nothing — it has to be
emitted on the wire — so this file asserts BOTH:

* the constant carries ``X-Content-Type-Options: nosniff`` and
  ``X-Frame-Options: DENY``, and a CSP that still permits the CDN origins the UI
  actually loads (jsDelivr, Google Fonts, TradingView) — a bare
  ``default-src 'self'`` would block every one of them and regress the pages;
* each send path emits those headers, verified by driving the real send methods
  with the HTTP transport stubbed (no browser, no socket).
"""

from __future__ import annotations

import io
import json

from jarvis.api import server as server_module


# The origins the live UI loads. Keep in step with the comment above the
# constant; a missing one means a page silently loses a script, a font or a
# chart library.
REQUIRED_CSP_ORIGINS = (
    "https://cdn.jsdelivr.net",
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
    "https://s3.tradingview.com",
)


class _RecordingHandler(server_module.JarvisRequestHandler):
    """Transport stubbed; the real send bodies run."""

    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.headers = {}
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self.status_codes = []
        self.sent_headers = []

    def send_response(self, code, message=None):
        self.status_codes.append(code)

    def send_header(self, keyword, value):
        self.sent_headers.append((keyword, value))

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self.status_codes.append(code)

    def log_message(self, *args, **kwargs):
        pass

    def header(self, name):
        lowered = name.lower()
        for key, value in self.sent_headers:
            if key.lower() == lowered:
                return value
        return None


def _csp(handler):
    return handler.header("Content-Security-Policy")


# ── the constant ────────────────────────────────────────────────────────────
def test_the_header_constant_pins_nosniff_and_deny():
    headers = dict(server_module.JarvisRequestHandler._SECURITY_HEADERS)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


def test_the_csp_permits_every_origin_the_dashboard_loads():
    headers = dict(server_module.JarvisRequestHandler._SECURITY_HEADERS)
    csp = headers["Content-Security-Policy"]
    for origin in REQUIRED_CSP_ORIGINS:
        assert origin in csp, f"CSP would block {origin}"


def test_the_csp_is_not_a_bare_self_default():
    """A regression to ``default-src 'self'`` alone would block the CDNs above."""
    headers = dict(server_module.JarvisRequestHandler._SECURITY_HEADERS)
    assert headers["Content-Security-Policy"] != "default-src 'self'"


# ── the send paths ──────────────────────────────────────────────────────────
def test_json_responses_carry_the_security_headers():
    handler = _RecordingHandler(base_dir=".")
    handler._send_json({"hello": "world"})

    assert handler.status_codes[-1] == 200
    assert handler.header("X-Content-Type-Options") == "nosniff"
    assert handler.header("X-Frame-Options") == "DENY"
    assert "https://cdn.jsdelivr.net" in (_csp(handler) or "")


def test_static_file_responses_carry_the_security_headers(tmp_path):
    static_dir = tmp_path / "ui" / "static"
    static_dir.mkdir(parents=True)
    asset = static_dir / "security_probe.css"
    asset.write_text("body { color: #fff; }\n", encoding="utf-8")

    handler = _RecordingHandler(base_dir=str(tmp_path))
    handler._STATIC_CACHE.pop("/static/security_probe.css", None)
    handler._serve_static_file("/static/security_probe.css")

    assert handler.status_codes[-1] == 200
    assert handler.header("X-Content-Type-Options") == "nosniff"
    assert handler.header("X-Frame-Options") == "DENY"
    assert "https://cdn.jsdelivr.net" in (_csp(handler) or "")


def test_template_responses_carry_the_security_headers(tmp_path):
    templates_dir = tmp_path / "ui" / "templates"
    templates_dir.mkdir(parents=True)
    template = templates_dir / "security_probe.html"
    template.write_text("<!doctype html><title>probe</title>\n", encoding="utf-8")

    handler = _RecordingHandler(base_dir=str(tmp_path))
    handler._serve_template("security_probe.html")

    assert handler.status_codes[-1] == 200
    assert handler.header("Content-Type") == "text/html; charset=utf-8"
    assert handler.header("X-Content-Type-Options") == "nosniff"
    assert handler.header("X-Frame-Options") == "DENY"
    assert "https://cdn.jsdelivr.net" in (_csp(handler) or "")
