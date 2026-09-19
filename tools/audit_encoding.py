"""Detect (and optionally repair) CP1252 double-encoded text in the UI tree.

The bug this catches
--------------------
A file that is UTF-8 gets read as CP1252 and written back as UTF-8. Every
non-ASCII character then survives as *two or three* characters of mojibake.
Written as escapes, because spelling the mojibake out literally in this file
would make this file a hit (which it did, on the first run):

    "\\u2014"  em dash          ->  "\\u00e2\\u20ac\\u201d"   three chars
    "\\u26a0"  warning sign     ->  "\\u00e2\\u0161\\u00a0"   two chars
    "\\u2713"  check mark       ->  "\\u00e2\\u0153\\u201c"
    "\\u2715"  multiplication x ->  "\\u00e2\\u0153\\u2022"
    "\\u26f6"  square corners   ->  "\\u00e2\\u203a\\u00b6"

The result is still a perfectly valid Python/JS string, so nothing raises and
no test that checks for a substring fails. It just renders as garbage in the
browser - which is how it survives: the only way to see it is to look at the
page, or to check the bytes.

Detection
---------
Not every high character is this bug - a genuine `Ã` in a name is a real `Ã`.
So a run is only reported when BOTH hold:

1. every character in the run exists in CP1252, and
2. re-encoding the run to CP1252 yields bytes that decode cleanly as UTF-8,
   and the result differs from the input.

Condition 2 is what makes this precise: it reconstructs the original byte
sequence, so a false positive would require the mojibake to *also* be valid
UTF-8 as bytes, which is what the round-trip actually produced.

A run must also contain at least one character from SMOKING - the set that
appears in mojibake but essentially never in English prose (â, Â, Ã, €, ‹, ›,
Œ, ž, š, œ, ž). Without that guard, runs of ordinary accented text that happen
to be CP1252-clean would be reported.

Usage (from the repo root):

    python tools/audit_encoding.py            # scan, report, exit 1 on a hit
    python tools/audit_encoding.py --fix      # repair in place
    python tools/audit_encoding.py --all      # include docs/ and .md files
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that never contain source we ship. `.workbuddy-ai` holds session
# memory (prose notes that quote the mojibake on purpose), so it is never part
# of the verdict.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".scratch", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
    ".workbuddy-ai",
}

# File types that are served to a browser or executed.
CODE_EXTS = {".py", ".js", ".css", ".html", ".json", ".yaml", ".yml",
             ".bat", ".cmd", ".ps1", ".sh"}
DOC_EXTS = {".md", ".txt", ".rst"}

# Characters that appear in CP1252 mojibake.
MOJI_CHARS = frozenset(
    "\u00c2\u00c3\u00e2\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030"
    "\u0160\u2039\u0152\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc"
    "\u2122\u0161\u203a\u0153\u017e\u0178\u00a0\u00a1\u00a2\u00a3\u00a4\u00a5"
    "\u00a6\u00a7\u00a8\u00a9\u00aa\u00ab\u00ac\u00ad\u00ae\u00af\u00b0\u00b1"
    "\u00b2\u00b3\u00b4\u00b5\u00b6\u00b7\u00b8\u00b9\u00ba\u00bb\u00bc\u00bd"
    "\u00be\u00bf\u00c0\u00c1\u00c4\u00c5\u00c6\u00c7\u00c8\u00c9\u00ca\u00cb"
    "\u00cc\u00cd\u00ce\u00cf\u00d0\u00d1\u00d2\u00d3\u00d4\u00d5\u00d6\u00d7"
    "\u00d8\u00d9\u00da\u00db\u00dc\u00dd\u00de\u00df\u00e0\u00e1\u00e3\u00e4"
    "\u00e5\u00e6\u00e7\u00e8\u00e9\u00ea\u00eb\u00ec\u00ed\u00ee\u00ef\u00f0"
    "\u00f1\u00f2\u00f3\u00f4\u00f5\u00f6\u00f7\u00f8\u00f9\u00fa\u00fb\u00fc"
    "\u00fd\u00fe\u00ff"
)

# The subset that is diagnostic on its own.
SMOKING = frozenset("\u00c2\u00c3\u00e2\u20ac\u201a\u0192\u201e\u2026\u2020"
                    "\u2021\u0160\u2039\u0152\u017d\u0161\u203a\u0153\u017e")


def repair(run: str) -> str | None:
    """Reverse the double-encode, or None when this is not that bug."""
    try:
        return run.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def runs_in(line: str):
    """Maximal runs of mojibake-eligible characters, with their offsets."""
    start = None
    for i, ch in enumerate(line):
        if ch in MOJI_CHARS:
            if start is None:
                start = i
        elif start is not None:
            yield start, line[start:i]
            start = None
    if start is not None:
        yield start, line[start:]


def find_in_line(line: str):
    """Yield (offset, bad, good) for every confirmed double-encode in a line."""
    for off, run in runs_in(line):
        if len(run) < 2 or not (set(run) & SMOKING):
            continue
        good = repair(run)
        if good and good != run:
            yield off, run, good


def iter_files(include_docs: bool):
    exts = CODE_EXTS | DOC_EXTS if include_docs else CODE_EXTS
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() in exts:
                yield os.path.join(dirpath, fn)


def scan_file(path: str):
    """Return (hits, unreadable). hits is a list of (lineno, bad, good)."""
    try:
        # newline="" is load-bearing. Opening in text mode without it turns on
        # universal-newline translation, which silently rewrites a CRLF file to
        # LF on the way in - so a repair pass would churn every line ending in
        # the file and bury the real change in a whole-file diff. This repo's
        # UI sources are CRLF.
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        return [], True
    except OSError:
        return [], False
    hits = []
    for i, line in enumerate(text.split("\n"), 1):
        for _off, bad, good in find_in_line(line):
            hits.append((i, bad, good))
    return hits, False


def apply_fix(path: str) -> int:
    """Rewrite the file with every confirmed run repaired. Returns runs fixed.

    Line endings are preserved exactly: splitting on "\\n" leaves any "\\r"
    attached to the end of its line, and rejoining with "\\n" puts it back.
    """
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.read().split("\n")
    fixed = 0
    for i, line in enumerate(lines):
        if not any(ch in SMOKING for ch in line):
            continue
        out, cursor = [], 0
        for off, bad, good in find_in_line(line):
            out.append(line[cursor:off])
            out.append(good)
            cursor = off + len(bad)
            fixed += 1
        if cursor:
            out.append(line[cursor:])
            lines[i] = "".join(out)
    if fixed:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines))
    return fixed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fix", action="store_true",
                    help="repair the confirmed runs in place")
    ap.add_argument("--all", action="store_true",
                    help="also scan docs (.md/.txt/.rst)")
    ap.add_argument("--verbose", action="store_true",
                    help="list every file scanned, clean or not")
    args = ap.parse_args()

    total = 0
    n_files = 0
    hits_by_file: dict[str, list] = {}
    unreadable: list[str] = []
    fixed_total = 0

    for path in iter_files(args.all):
        n_files += 1
        rel = os.path.relpath(path, ROOT)
        hits, bad_encoding = scan_file(path)
        if bad_encoding:
            unreadable.append(rel)
            continue
        if hits:
            hits_by_file[rel] = hits
            total += len(hits)
            if args.fix:
                fixed_total += apply_fix(path)
        elif args.verbose:
            print(f"  ok    {rel}")

    print("=" * 78)
    print(f"scanned {n_files} file(s)")
    print("=" * 78)

    if hits_by_file:
        print()
        for rel in sorted(hits_by_file):
            rows = hits_by_file[rel]
            verb = "fixed" if args.fix else "found"
            print(f"{rel}  ({len(rows)} {verb})")
            for ln, bad, good in rows[:10]:
                print(f"    line {ln}: {bad!r} -> {good!r}")
            if len(rows) > 10:
                print(f"    ... and {len(rows) - 10} more")
            print()

    if unreadable:
        print("not valid UTF-8 (fix the encoding before anything else):")
        for rel in unreadable:
            print(f"    {rel}")
        print()

    if args.fix:
        print(f"repaired {fixed_total} run(s)")
        remaining = 0
        for path in iter_files(args.all):
            h, _ = scan_file(path)
            remaining += len(h)
        print(f"re-scan: {remaining} run(s) remaining")
        verdict_ok = remaining == 0 and not unreadable
    else:
        verdict_ok = total == 0 and not unreadable

    print("=" * 78)
    print(f"VERDICT: {total} double-encoded run(s) in {len(hits_by_file)} file(s), "
          f"{len(unreadable)} file(s) not valid UTF-8")
    print("=" * 78)
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
