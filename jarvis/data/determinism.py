"""
JARVIS AI 3.0 — Deterministic seeding for synthetic/modelled values.

Several engines synthesise plausible-looking series and profile fields (candle
walks, option-chain skew and OI, earnings dates, implied volatility, surveillance
status) rather than reading them from a feed. Those values must at least be
*stable*: the same instrument has to produce the same number on every process,
or a user sees a symbol's earnings date or F&O status change on every restart.

The obvious tool for that, ``hash()``, is exactly wrong: CPython salts it per
process (PYTHONHASHSEED), so a hash-derived seed differs on every interpreter
start. ``stable_seed`` is the single replacement — a plain function of the text,
identical everywhere.
"""


def stable_seed(text: str) -> int:
    """
    Deterministic seed for a symbol/text, identical in every process.

    NOT ``hash()``: CPython salts ``hash()`` per process (PYTHONHASHSEED), so a
    hash-derived seed yields a different value on every interpreter start — the
    opposite of what a "stable" sample needs.

    Callers that need a per-instrument RNG should seed a *local* generator
    (``random.Random(stable_seed(...))`` or ``np.random.RandomState(...)``)
    rather than reseeding the module-level ``random`` generator, which leaks into
    every other caller of ``random`` in the process.
    """
    return sum((i + 1) * ord(c) for i, c in enumerate(text)) % 100000
