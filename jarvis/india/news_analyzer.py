"""
JARVIS AI 3.0 — India Institutional News & FII / DII Flow Analyzer
Synthesizes Indian macroeconomic indicators, RBI policy decisions, corporate quarterly results,
and real-time Foreign & Domestic Institutional Investors (FII / DII) buying & selling cash data.
"""
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
import random

from jarvis.data.determinism import stable_seed


class IndiaNewsAnalyzer:
    """
    Indian Market News, Corporate Actions, and FII/DII Institutional Flow Tracker.
    """

    def get_fii_dii_flows(self) -> Dict[str, Any]:
        """
        Returns FII & DII cash and index-derivative positioning.

        PROVENANCE: no live FII/DII feed is connected to this deployment. The
        figures below are fixed sample values, not today's provisional cash
        numbers. `data_source` states that explicitly and every consumer is
        expected to render it: an institutional-flow panel that presents
        invented numbers as today's prints misleads a trader about where the
        money actually went, which is worse than showing no panel at all.
        """
        now = datetime.now(timezone.utc)
        return {
            "date": now.strftime("%d-%b-%Y"),
            "data_source": "sample",
            "data_source_note": (
                "Fixed sample values. No live FII/DII feed is connected; these "
                "are not today's cash-market flows."
            ),
            "fii_cash_net_cr": 1845.50,
            "dii_cash_net_cr": 2410.20,
            "total_net_institutional_cr": 4255.70,
            "fii_index_futures_long_pct": 68.5,
            "fii_index_options_pcr": 1.22,
            "fii_sentiment": "NET_BUYERS",
            "dii_sentiment": "STRONG_DOMESTIC_INFLOWS",
            "institutional_bias": "STRONG_BULLISH_SUPPORT"
        }

    def get_stock_news(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Generates realistic corporate announcements, board resolutions, and earnings updates for Indian equities.
        """
        sym = (symbol or "NIFTY").upper().strip()

        headline_templates = [
            (f"{sym} reports 24% YoY surge in consolidated net profit, declares ₹18/share interim dividend", "BULLISH", 0.88, "2 hours ago", "NSE Corporate Filing"),
            (f"FIIs increase stake in {sym} by 140 bps following strong quarterly operating margins", "BULLISH", 0.79, "4 hours ago", "Moneycontrol / Bloomberg Quint"),
            (f"{sym} bags mega multi-year ₹3,400 Cr defense and clean infrastructure execution mandate", "BULLISH", 0.92, "7 hours ago", "Economic Times"),
            (f"SEBI approves revised expansion framework for {sym} derivative contract liquidity", "BULLISH", 0.74, "12 hours ago", "LiveMint"),
            (f"Management of {sym} affirms strong guidance with order book exceeding ₹45,000 Cr", "BULLISH", 0.85, "1 day ago", "CNBC-TV18")
        ]

        # A local RNG seeded from the symbol, so the same instrument yields the
        # same sample across runs. The previous form seeded the module-level
        # generator, which leaked into every other caller of `random` in the
        # process — including the options engine's IV rank, making that value
        # depend on which symbol happened to be queried last. `hash()` is also
        # randomised per process, so the "stable" seed was not stable; see
        # `stable_seed`.
        seed = stable_seed(sym)
        rng = random.Random(seed)
        selected = rng.sample(headline_templates, min(4, len(headline_templates)))

        news_items = []
        for h, sent, score, time_ago, src in selected:
            news_items.append({
                "headline": h,
                "sentiment": sent,
                "sentiment_score": score,
                "time_ago": time_ago,
                "source": src,
                "summary": f"Institutional analysts view the development as a major medium-term catalyst strengthening price discovery on NSE.",
                # PROVENANCE: these are generated from templates above, not read
                # from a news wire. The UI must label them as sample copy.
                "data_source": "sample"
            })

        return news_items


INDIA_NEWS = IndiaNewsAnalyzer()
