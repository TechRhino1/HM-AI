#!/usr/bin/env python3
"""Audit CSS custom properties: every `var(--x)` must resolve to a definition.

A `var()` naming a token that does not exist does not throw, warn, or appear in
any console - the whole declaration is simply dropped as invalid at computed-value
time. A mistyped `background` therefore renders TRANSPARENT, and a mistyped
`border-radius` renders square, with nothing anywhere to say so.

Found live 2026-09-17: the copilot dock was written with `--hm-bg-panel` (never
defined), so it had no background at all and the order ticket's stop-loss and
take-profit fields showed straight through the chat log. `--hm-radius-md` and
`--hm-text` were the same mistake in the same block. No test in this repo could
see any of it.

Usage:  python tools/audit_css_tokens.py [--quiet]
Exit 1 if any `var()` is undefined.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_DIR = ROOT / "jarvis" / "ui" / "static" / "css"

# A definition is `--name:` anywhere (typically in a `:root` block). Values may
# themselves reference other tokens, which is fine - resolution is transitive.
DEF_RE = re.compile(r"(--[a-zA-Z0-9_-]+)\s*:")
USE_RE = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)")

# Fallback-only usage (`var(--x, 8px)`) degrades gracefully, so it is reported
# separately rather than failed - a missing token there is a smell, not a bug.
FALLBACK_RE = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)\s*,")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    files = sorted(CSS_DIR.glob("*.css"))
    if not files:
        print(f"no CSS found under {CSS_DIR}")
        return 1

    defined: set[str] = set()
    for f in files:
        defined.update(DEF_RE.findall(f.read_text(encoding="utf-8", errors="replace")))

    undefined: dict[str, list[str]] = {}
    fallback_only: dict[str, list[str]] = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        with_fallback = set(FALLBACK_RE.findall(text))
        for name in USE_RE.findall(text):
            if name in defined:
                continue
            bucket = fallback_only if name in with_fallback else undefined
            bucket.setdefault(name, [])
            if f.name not in bucket[name]:
                bucket[name].append(f.name)

    print(f"css files      : {len(files)}")
    print(f"tokens defined : {len(defined)}")
    print(f"UNDEFINED      : {len(undefined)}")
    for name, where in sorted(undefined.items()):
        print(f"  {name:26s} {', '.join(where)}")
        print("      -> the declaration is DROPPED; check the intended token name")
    if fallback_only and not args.quiet:
        print(f"\nfallback-only  : {len(fallback_only)} (degrade gracefully, not a failure)")
        for name, where in sorted(fallback_only.items()):
            print(f"  {name:26s} {', '.join(where)}")

    return 1 if undefined else 0


if __name__ == "__main__":
    sys.exit(main())
