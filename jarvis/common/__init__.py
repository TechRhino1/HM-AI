"""Shared, dependency-free primitives usable by every JARVIS layer.

`jarvis.common` is a leaf package: it must not import from any other
`jarvis.*` package, so that lower layers (market, data, analysts, execution)
can depend on it without creating upward edges.
"""
