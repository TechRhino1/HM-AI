#!/usr/bin/env python3
"""Scan the repo for leftover traces of the old product brand.

Written after a plain-text grep missed four occurrences. The stocks/india/
india_options/index HUDs carried the brand SPLIT ACROSS MARKUP:

    <div class="brand-text">HM <span>AI 4.0</span> • Stock Intelligence</div>

No regex over the raw file can match `HM AI 4.0` there, because ` <span>` sits
between the two halves. A text-only scan therefore reported "0 leftovers" while
the user could still read the old name on four of six pages. This scanner
searches BOTH the raw text and the tag-stripped text of every markup file.

What is deliberately NOT reported, because renaming it was an explicit decision
("display name only") or would break the system:

  * `jarvis/`                  the Python package and every import of it
  * `Jarvis*`                  class names (JarvisOrchestrator)
  * `JARVIS_*`                 constants, logger names, the MT5 order comment
  * `jarvis_*.db`              state-file names
  * `HM_AI` / `HM-AI`          filesystem paths
  * `J3_` / `HMA2_`            order-routing comment prefixes

Usage:  python tools/scan_brand.py [--quiet]
Exit 1 when anything user-visible is found.
"""
import argparse
import os
import re
import sys

# The old brand, in the forms that should no longer exist. The tag-stripped pass
# is what catches markup-split occurrences, so these are plain text patterns.
OLD_BRAND = [
    (re.compile(r"\bJARVIS\b(?!_)"), "bare JARVIS"),
    (re.compile(r"HM\s+AI\s+[0-9]+\.[0-9]+"), "HM AI <version>"),
    (re.compile(r"\bAI\s+[34]\.0\b"), "AI 4.0 / AI 3.0 fragment"),
    (re.compile(r"HM\s+AI(?![\w.])"), "HM AI (no version)"),
]

# Allowed, with the reason. Reported as suppressed so the count is honest.
PRESERVED = [
    (re.compile(r"JARVIS_[A-Za-z0-9_]+"), "JARVIS_* identifier"),
    (re.compile(r"\bjarvis\b"), "jarvis package / db name"),
    (re.compile(r"\bJarvis[A-Za-z]"), "Jarvis* class name"),
    (re.compile(r"HM[_-]AI"), "HM_AI path"),
]

SKIP_DIRS = {".git", "data", "node_modules", ".scratch", "__pycache__",
             ".pytest_cache", "venv", ".venv", "reports"}

# Files that legitimately CONTAIN the old brand because their subject IS the
# rename: this scanner's own pattern table, and the work journal that records
# what was renamed. Neither is product surface.
SKIP_FILES = {
    "tools/scan_brand.py",
}
SKIP_PATH_PARTS = (
    ".workbuddy-ai/memory/",
    "AUDIT-2026-09.md",
)
TEXT_EXT = {".py", ".js", ".css", ".html", ".md", ".txt", ".json", ".norm",
            ".bat", ".cmd", ".ps1", ".example", ".ini", ".cfg", ".toml",
            ".log", ".yml", ".yaml"}

TAG_RX = re.compile(r"<[^>]+>")
SCRIPT_RX = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in TEXT_EXT:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if rel in SKIP_FILES or any(p in rel for p in SKIP_PATH_PARTS):
                continue
            yield full


def stripped(text):
    """Markup with tags removed and entities unwrapped, so a brand split across
    an element boundary becomes contiguous again."""
    text = SCRIPT_RX.sub(" ", text)
    text = TAG_RX.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t]+", " ", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    # Parsed for CLI validation / --help only. NOTE: the --quiet flag is currently a no-op —
    # it is accepted but never read (pre-existing; reported during lint cleanup, not changed here).
    ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits, preserved = [], 0

    for path in iter_files(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                raw = fh.read()
        except OSError:
            continue

        is_markup = os.path.splitext(path)[1].lower() in {".html", ".htm"}
        variants = [("raw", raw)]
        if is_markup:
            variants.append(("text", stripped(raw)))

        for how, body in variants:
            for rx, label in OLD_BRAND:
                for m in rx.finditer(body):
                    line = body[:m.start()].count("\n") + 1
                    snippet = body[max(0, m.start() - 40):m.end() + 40]
                    snippet = " ".join(snippet.split())
                    hits.append((rel, line, label, how, snippet))

        for rx, _label in PRESERVED:
            preserved += len(rx.findall(raw))

    seen, uniq = set(), []
    for h in hits:
        key = (h[0], h[2], h[4])
        if key not in seen:
            seen.add(key)
            uniq.append(h)

    if uniq:
        print(f"OLD BRAND STILL PRESENT: {len(uniq)} site(s)\n")
        for rel, line, label, how, snippet in uniq:
            print(f"  {rel}:{line}  [{label}, matched in {how}]")
            print(f"      …{snippet}…")
        print()
    else:
        print("No leftover occurrences of the old brand in any file.\n")

    print(f"Preserved by design (not leftovers): {preserved} occurrences of "
          f"JARVIS_*/jarvis/Jarvis*/HM_AI.")
    return 1 if uniq else 0


if __name__ == "__main__":
    sys.exit(main())
