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


class TestNewViewsAreWired(unittest.TestCase):
    """A view is reachable only if three things agree: the rail has a button, the
    template has a panel, and the controller's VIEWS array accepts the name.
    Any one of them missing leaves the tab either absent or inert, and inert is
    the dangerous case — the tab renders, the click does nothing, and nothing
    raises."""

    NEW_VIEWS = ("news", "analyst", "markets")

    def test_rail_has_a_button_and_a_panel_for_each_view(self):
        html = _read(os.path.join(TEMPLATE_DIR, "dashboard.html"))
        for view in self.NEW_VIEWS:
            self.assertIn(f'data-view-btn="{view}"', html, f"no rail tab for {view}")
            self.assertIn(f'data-view-panel="{view}"', html, f"no panel for {view}")
            self.assertIn(f'id="view-{view}"', html, f"no section for {view}")

    def test_controller_accepts_each_view(self):
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        match = re.search(r"var VIEWS = \[([^\]]*)\]", js)
        self.assertIsNotNone(match, "VIEWS array not found in dashboard.js")
        declared = {v.strip().strip("'\"") for v in match.group(1).split(",") if v.strip()}
        for view in self.NEW_VIEWS:
            self.assertIn(view, declared, f"setView() would reject '{view}'")

    def test_each_view_has_a_loader(self):
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        for fn in ("loadNews", "renderDevilAdvocate", "renderQualityGate", "loadMarkets"):
            self.assertIn(f"function {fn}(", js, f"missing {fn}()")

    def test_views_open_without_a_page_reload(self):
        """Switching to a view must fetch that view's data, not assume it is
        already loaded."""
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        for call in ("if (view === 'news')", "if (view === 'analyst')",
                     "if (view === 'markets')"):
            self.assertIn(call, js, f"setView does not load on: {call}")


class TestCalendarCountdownIsDerivedLocally(unittest.TestCase):
    """The server sends both ``diff_seconds`` and a ``status_badge`` string like
    "IN 14h 33m", computed at generation time. Rendering the badge would freeze
    the countdown between polls and leave it reading "IN 0m" indefinitely, so
    the controller must anchor to each event's own timestamp instead."""

    def test_countdown_is_not_the_server_badge(self):
        """Pin the *read*, not the identifier: the string legitimately appears
        in a comment explaining why the badge is not used. What must never
        appear is a property access that pulls the badge out of a payload."""
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        for read in (".status_badge", "['status_badge']", '["status_badge"]'):
            self.assertNotIn(
                read, js,
                f"the countdown must be recomputed locally, not read from the "
                f"server-computed badge via {read}, which goes stale the moment "
                f"it arrives",
            )

    def test_countdown_anchors_to_the_event_timestamp(self):
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        self.assertIn("timestamp_iso", js)
        self.assertIn("diff_seconds", js)
        self.assertIn("Date.parse", js)

    def test_client_live_window_matches_the_backend(self):
        """The client applies the backend's own window, so a row flips to live
        and then to released while the page is open rather than holding the
        payload's snapshot. If either side moves, the two disagree."""
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        self.assertIn("NEWS_LIVE_BEFORE = 300", js)
        self.assertIn("NEWS_LIVE_AFTER = 900", js)

        news = _read(os.path.join(REPO_ROOT, "jarvis", "market", "news.py"))
        self.assertIn("timedelta(minutes=5)", news)
        self.assertIn("timedelta(minutes=15)", news)


class TestFabricatedValuesAreFlagged(unittest.TestCase):
    """Every response that returns modelled, fixed or placeholder values must
    say so. A UI cannot label what the backend does not describe, and a
    plausible number presented as a live quote is worse than no number at all."""

    def test_fii_dii_is_flagged_as_sample(self):
        from jarvis.india.news_analyzer import IndiaNewsAnalyzer

        payload = IndiaNewsAnalyzer().get_fii_dii_flows()
        self.assertEqual(payload["data_source"], "sample")
        self.assertTrue(payload.get("data_source_note"),
                        "the sample flag must carry an explanation the UI can show")

    def test_india_stock_news_is_flagged_as_sample(self):
        from jarvis.india.news_analyzer import IndiaNewsAnalyzer

        items = IndiaNewsAnalyzer().get_stock_news("RELIANCE")
        self.assertTrue(items, "expected generated items")
        self.assertTrue(all(i["data_source"] == "sample" for i in items))

    def test_us_stock_news_is_flagged_as_sample(self):
        from jarvis.stocks.news_analyzer import StockNewsAnalyzer

        items = StockNewsAnalyzer().get_stock_news("NVDA")
        self.assertTrue(items, "expected generated items")
        self.assertTrue(all(i["data_source"] == "sample" for i in items))

    def test_screener_flags_fallback_rows(self):
        src = _read(os.path.join(REPO_ROOT, "jarvis", "stocks", "stock_service.py"))
        self.assertIn('"analysis_source": "fallback"', src)
        self.assertIn('"analysis_source": "computed"', src)
        self.assertIn('"fallback_count"', src)

    def test_india_indices_propagate_data_source(self):
        src = _read(os.path.join(REPO_ROOT, "jarvis", "india", "india_service.py"))
        self.assertIn('"data_source": row_source', src)
        self.assertIn('row_source = "profile_reference"', src)

    def test_renderer_suppresses_placeholder_setup_fields(self):
        """The renderer must gate the setup columns on the row's provenance, so
        a failed analysis cannot be read as a computed setup. Assert the
        rendered strings, not the JS quoting around them."""
        js = _read(os.path.join(JS_DIR, "dashboard.js"))
        self.assertIn("analysis_source", js)
        self.assertIn("'fallback'", js)
        # The setup cell says the symbol was not analysed rather than showing a
        # grade, and the price is marked as a reference rather than a quote.
        self.assertIn(">no analysis</span>", js)
        self.assertIn(" ref</span>", js)


class TestNewsGeneratorDoesNotDisturbGlobalRandom(unittest.TestCase):
    """The generator used to call ``random.seed()``, which reseeds the
    module-level RNG for the whole process — including the options engine's IV
    rank, so that value depended on which symbol happened to be queried last. It
    seeded from ``hash()`` too, which is randomised per process, so the "stable"
    seed was not stable."""

    def test_module_level_random_state_is_untouched(self):
        import random

        from jarvis.india.news_analyzer import IndiaNewsAnalyzer

        random.seed(12345)
        before = random.random()
        random.seed(12345)
        IndiaNewsAnalyzer().get_stock_news("RELIANCE")
        after = random.random()
        self.assertEqual(
            before, after,
            "get_stock_news() consumed from the module-level RNG, which leaks "
            "into every other caller of random in the process",
        )

    def test_output_is_stable_for_the_same_symbol(self):
        from jarvis.india.news_analyzer import IndiaNewsAnalyzer

        analyzer = IndiaNewsAnalyzer()
        first = analyzer.get_stock_news("TCS")
        second = analyzer.get_stock_news("TCS")
        self.assertEqual([i["headline"] for i in first], [i["headline"] for i in second])

    def test_source_no_longer_seeds_the_global_generator(self):
        src = _read(os.path.join(REPO_ROOT, "jarvis", "india", "news_analyzer.py"))
        self.assertNotIn("random.seed(", src)
        self.assertIn("random.Random(seed)", src)


if __name__ == "__main__":
    unittest.main()
