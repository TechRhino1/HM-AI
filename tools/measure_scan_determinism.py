"""Is the §J scan deterministic? Measure it, do not assume it.

The earlier sweep concluded "the lever is noise" from a harness that leaked its
registry override. The fixed harness now says ADVERSE at every tp_r. Before
believing the new number, test the instrument: scan the SAME symbol under the
SAME registry twice in one process and compare.

Three AUDUSD A-arm values have already been seen for what should be one
measurement -- 51 (probe), 53 (fixed sweep), 56 (universe run). If those are
real, the scanner is non-deterministic and the cause is known to be reachable:
`ParallelAnalystCluster` allows the MACRO analyst 2.0s while
`jarvis/market/news.py` allows the fetch it calls 5s and 6s, so a cache miss
(90s TTL) replaces MACRO with a fabricated score-50 NEUTRAL reading.

This probe counts those fallbacks so the two facts are tied together.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import spread_registry_ab as ab  # noqa: E402

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "AUDUSD"
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 2


class Counter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.fallback = 0
        self.devil = 0

    def emit(self, record):
        msg = record.getMessage()
        if "NEUTRAL score-50 fallback" in msg:
            self.fallback += 1
        elif "uncriticised" in msg:
            self.devil += 1


counter = Counter()
logging.getLogger("JARVIS_AnalystCluster").addHandler(counter)
logging.getLogger("JARVIS_AnalystCluster").setLevel(logging.WARNING)

df = ab.load_bars(SYMBOL, "H1", 183)
if df is None:
    raise SystemExit(f"no bars for {SYMBOL}")

print(f"symbol={SYMBOL}  bars={len(df)}  reps={REPS}  registry=PRISTINE")
results = []
for i in range(REPS):
    ab.apply_registry(None)
    before = counter.fallback
    res = ab.scan(SYMBOL, df)
    n = int(res.executed)
    results.append(n)
    print(f"  rep {i + 1}: EXEC={n:5d}  candidates={len(res.candidates) if res.candidates is not None else 0:5d} "
          f" MACRO_fallbacks_this_rep={counter.fallback - before}")

print()
print(f"EXEC values: {results}")
print(f"scan deterministic: {len(set(results)) == 1}")
print(f"total MACRO fallbacks: {counter.fallback}   devil fallbacks: {counter.devil}")
