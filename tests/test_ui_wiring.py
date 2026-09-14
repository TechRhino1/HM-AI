"""
UI <-> API wiring contract for the redesigned dashboard.

Each test here pins a defect that was real, silent, and invisible to every
existing test:

1. dashboard.js asked /api/candles for ``timeframe=`` while the handler read
   ``tf=``. The endpoint answered 200 with H1 candles every time, so the
   timeframe selector looked like it worked and did nothing. A 200 is not
   evidence that a parameter was honoured.

2. renderPositions read ``p.price_open`` / ``p.price_current``, but the backend
   serialises ``open_price`` / ``current_price``. ``num(undefined)`` renders a
   dash, so the Entry and Now columns were permanently blank with no error.

3. The dashboard controller queried element ids that the template need not
   define. ``getElementById`` returns null for a missing id and the update is
   silently skipped, so a panel can ship completely dead while every HTTP check
   passes. The ids the controller uses are therefore asserted to exist.

These are cheap, source-level invariants: no server, no browser, no fixtures.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(REPO_ROOT, "jarvis", "ui")
JS_DIR = os.path.join(UI, "static", "js")
TEMPLATE_DIR = os.path.join(UI, "templates")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestCandlesQueryParameter(unittest.TestCase):
    """Every UI caller must send the parameter the handler actually reads."""

    def test_no_caller_uses_the_unread_parameter_name(self):
        offenders = {}
        for name in sorted(os.listdir(JS_DIR)):
            if not name.endswith(".js"):
                continue
            src = _read(os.path.join(JS_DIR, name))
            for match in re.finditer(r"/api/candles\?[^'\"`\s]*", src):
                url = match.group(0)
                if "&timeframe=" in url or url.endswith("?timeframe="):
                    offenders.setdefault(name, []).append(url[:80])
        self.assertEqual(
            offenders, {},
            f"these calls use a parameter /api/candles does not read: {offenders}",
        )

    def test_every_caller_sends_tf(self):
        callers = []
        for name in sorted(os.listdir(JS_DIR)):
            if not name.endswith(".js"):
                continue
            src = _read(os.path.join(JS_DIR, name))
            if "/api/candles?" in src:
                callers.append((name, "&tf=" in src or "?tf=" in src))
        self.assertTrue(callers, "expected at least one caller of /api/candles")
        missing = [n for n, ok in callers if not ok]
        self.assertEqual(missing, [], f"callers not sending tf=: {missing}")

    def test_handler_accepts_both_spellings(self):
        """The route should tolerate the longer name like its siblings do."""
        src = _read(os.path.join(REPO_ROOT, "jarvis", "api", "server.py"))
        self.assertIn(
            'query.get("tf", query.get("timeframe"', src,
            "/api/candles should accept both tf and timeframe, as /api/rates does",
        )


class TestPositionFieldNames(unittest.TestCase):
    """The dashboard must read the field names the backend actually serialises."""

    def test_dashboard_uses_serialised_position_fields(self):
        src = _read(os.path.join(JS_DIR, "dashboard.js"))
        self.assertNotIn("p.price_open", src)
        self.assertNotIn("p.price_current", src)
        self.assertIn("p.open_price", src)
        self.assertIn("p.current_price", src)

    def test_schema_serialises_those_exact_names(self):
        """Guard the guard: if the schema renames, the dashboard breaks again."""
        src = _read(os.path.join(REPO_ROOT, "jarvis", "data", "schemas.py"))
        self.assertIn('"open_price": self.open_price', src)
        self.assertIn('"current_price": self.current_price', src)


class TestDashboardElementIds(unittest.TestCase):
    """Ids the controller queries must exist in the template it drives."""

    def _queried_ids(self, js_source):
        ids = set()
        for pattern in (r"\$\('([A-Za-z0-9_-]+)'\)",
                        r"getElementById\('([A-Za-z0-9_-]+)'\)",
                        r'getElementById\("([A-Za-z0-9_-]+)"\)'):
            ids.update(re.findall(pattern, js_source))
        return ids

    def test_every_queried_id_exists_in_the_template(self):
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        html = _read(os.path.join(TEMPLATE_DIR, "dashboard.html"))
        template_ids = set(re.findall(r'\bid="([^"]+)"', html))

        missing = sorted(self._queried_ids(js) - template_ids)
        self.assertEqual(
            missing, [],
            f"dashboard.js queries ids that dashboard.html does not define: {missing}",
        )

    def test_chart_surface_is_present(self):
        """The chart is built at runtime, so a missing container is silent."""
        html = _read(os.path.join(TEMPLATE_DIR, "dashboard.html"))
        for element_id in ("chart", "chart-overlay", "chart-hud", "chart-tooltip",
                           "chart-legend", "chart-levels", "chart-live-price"):
            self.assertIn(f'id="{element_id}"', html, f"missing chart element: {element_id}")

    def test_restored_panels_are_present(self):
        html = _read(os.path.join(TEMPLATE_DIR, "dashboard.html"))
        for element_id in ("radar-body", "radar-count", "radar-filter",
                           "pending-body", "pending-count"):
            self.assertIn(f'id="{element_id}"', html, f"missing restored panel: {element_id}")


if __name__ == "__main__":
    unittest.main()
