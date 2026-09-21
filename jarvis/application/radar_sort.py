"""Single source of truth for radar-opportunity ordering.

Three copies of this sort key used to live in ``orchestrator.py`` and
``server.py`` (orchestrator backlog bug A1). They had drifted apart:

* the orchestrator copy returned a 5-tuple that weighted ``utility_score``;
* the two ``server.py`` copies returned 4-tuples (no ``utility_score``);
* only the read endpoint fell back to ``status_label`` when ``action`` was empty.

Because ``state_manager.update_radar`` replaces the *whole* list, whichever
producer ran last set the stored order, and the endpoint re-sorted on every
read with the *least* informative key — so the order the client saw was not the
one the producer intended. Consolidating here guarantees that every producer
and the consumer rank opportunities by the same rule.
"""
from typing import Any, Dict, Tuple


def radar_sort_key(item: Dict[str, Any]) -> Tuple[int, int, float, Any, Any]:
    """Rank a radar opportunity for display. Callers sort with ``reverse=True``.

    Higher values sort first:

    * ``is_open`` — actionable/open setups (0) beat closed ones (1);
    * ``conv``    — conviction: READY(3) > WAIT(2) > NO TRADE/INVALID(1) > other(0);
    * ``util``    — the arbiter's utility score (0.0 when a producer omits it);
    * ``prob``    — win probability, falling back to the generic score;
    * ``ev``      — expected value in R.

    Both ``action`` and ``status_label`` are consulted for the verdict, since
    the two producers name the field differently but mean the same thing.
    """
    act = str(item.get("action", "") or item.get("status_label", "") or "")
    is_open = 0 if "CLOSED" in act else 1
    if "READY" in act:
        conv = 3
    elif "WAIT" in act:
        conv = 2
    elif "NO TRADE" in act or "INVALID" in act:
        conv = 1
    else:
        conv = 0
    util = float(item.get("utility_score", 0.0) or 0.0)
    prob = item.get("win_prob", 0) or item.get("score", 0) or 0
    ev = item.get("ev", 0) or 0
    return (is_open, conv, util, prob, ev)
