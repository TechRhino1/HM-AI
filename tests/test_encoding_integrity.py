"""Guard against CP1252 double-encoded text anywhere in the shipped tree.

Why this exists
---------------
A UTF-8 source file that is read as CP1252 and written back as UTF-8 keeps
every non-ASCII character, but as two or three garbage characters. Nothing
raises, no import fails, and no substring assertion notices - the string is
still a valid string. It only shows up when a human looks at the page.

It had already happened in `jarvis/ui/static/js/terminal.js`, on five lines.
The one that mattered was the Expand button:

    btn.innerHTML = isExpanded ? "<garbage> Minimize" : "<garbage> Expand";

so pressing Expand rewrote the button as mojibake. The other four were the
crosshair tooltip's structure tags and a file-header comment.

Two of the tests below pin down behaviour that a naive repair gets wrong, and
both were found the hard way while writing the fixer:

* **A run must be re-encodable to CP1252 *and* decode as UTF-8.** Without the
  second half, ordinary accented text is reported as corruption.
* **Line endings must survive a repair.** `open(..., encoding='utf-8')` without
  `newline=''` enables universal-newline translation, so repairing a CRLF file
  rewrites all 3429 of its line endings and buries a five-line change in a
  whole-file diff. The first version of this tool did exactly that.

Every expected value here is derived from the CP1252 byte mapping by hand, not
copied from a tool run.
"""

from __future__ import annotations

import os

import pytest

from tools.audit_encoding import (
    ROOT,
    apply_fix,
    find_in_line,
    repair,
    scan_file,
)

# Hand-derived: these are the CP1252 bytes of the mojibake, read back as UTF-8.
EM_DASH_BAD = "\u00e2\u20ac\u201d"          # -> b"\xe2\x80\x94" -> U+2014
WARN_BAD = "\u00e2\u0161\u00a0"            # -> b"\xe2\x9a\xa0" -> U+26A0
CHECK_BAD = "\u00e2\u0153\u201c"           # -> b"\xe2\x9c\x93" -> U+2713
CROSS_BAD = "\u00e2\u0153\u2022"           # -> b"\xe2\x9c\x95" -> U+2715
SQUARE_BAD = "\u00e2\u203a\u00b6"          # -> b"\xe2\x9b\xb6" -> U+26f6


# ── the mapping itself ───────────────────────────────────────────────────────
@pytest.mark.parametrize("bad,good", [
    (EM_DASH_BAD, "\u2014"),
    (WARN_BAD, "\u26a0"),
    (CHECK_BAD, "\u2713"),
    (CROSS_BAD, "\u2715"),
    (SQUARE_BAD, "\u26f6"),
])
def test_repair_reverses_the_round_trip(bad, good):
    assert repair(bad) == good


@pytest.mark.parametrize("text", [
    "plain ascii",
    "caf\u00e9",                 # single accented char: run length 1
    "na\u00efve",
    "Stra\u00dfe",
    "\u00e9\u00e8",              # CP1252-clean pair, but not valid UTF-8
    "\u00e2\u20ac",              # truncated 3-byte sequence
])
def test_genuine_text_is_not_reported(text):
    assert list(find_in_line(text)) == []


def test_a_confirmed_run_is_located_by_offset():
    line = "tag = \"" + WARN_BAD + " Approaching\""
    hits = list(find_in_line(line))
    assert len(hits) == 1
    off, bad, good = hits[0]
    assert bad == WARN_BAD
    assert good == "\u26a0"
    assert line[off:off + len(bad)] == WARN_BAD


def test_two_runs_on_one_line_are_both_found():
    line = '"' + CROSS_BAD + ' Minimize" : "' + SQUARE_BAD + ' Expand"'
    assert [g for _o, _b, g in find_in_line(line)] == ["\u2715", "\u26f6"]


# ── the fixer must not churn anything else ───────────────────────────────────
def test_apply_fix_preserves_crlf_line_endings(tmp_path):
    """The trap that cost a whole-file diff the first time round."""
    p = tmp_path / "sample.js"
    p.write_bytes(("a\r\nb = \"" + CHECK_BAD + "\"\r\nc\r\n").encode("utf-8"))
    before = p.read_bytes()
    assert before.count(b"\r\n") == 3

    assert apply_fix(str(p)) == 1

    after = p.read_bytes()
    assert after.count(b"\r\n") == 3, "line endings were rewritten"
    assert after.count(b"\n") - after.count(b"\r\n") == 0
    assert b"\xe2\x9c\x93" in after, "the repair did not land"
    assert CHECK_BAD.encode("utf-8") not in after


def test_apply_fix_preserves_lf_line_endings(tmp_path):
    p = tmp_path / "sample.js"
    p.write_bytes(("a\nb = \"" + CHECK_BAD + "\"\nc\n").encode("utf-8"))
    assert apply_fix(str(p)) == 1
    after = p.read_bytes()
    assert after.count(b"\r\n") == 0
    assert after.count(b"\n") == 3


def test_apply_fix_is_idempotent(tmp_path):
    p = tmp_path / "sample.js"
    p.write_bytes(("x = \"" + EM_DASH_BAD + "\"\n").encode("utf-8"))
    assert apply_fix(str(p)) == 1
    once = p.read_bytes()
    assert apply_fix(str(p)) == 0, "a second pass reported work to do"
    assert p.read_bytes() == once


def test_apply_fix_leaves_a_clean_file_untouched(tmp_path):
    p = tmp_path / "sample.js"
    original = "const s = \"caf\u00e9 \u2014 na\u00efve\";\n".encode("utf-8")
    p.write_bytes(original)
    assert apply_fix(str(p)) == 0
    assert p.read_bytes() == original


# ── the tree itself ──────────────────────────────────────────────────────────
def _walk():
    from tools.audit_encoding import iter_files
    return list(iter_files(include_docs=False))


def test_scanner_actually_reaches_the_ui_sources():
    """A scan of nothing passes trivially, so assert it saw real files."""
    files = _walk()
    assert len(files) > 200, f"only {len(files)} files scanned - the walk is wrong"
    rels = {os.path.relpath(f, ROOT).replace(os.sep, "/") for f in files}
    assert "jarvis/ui/static/js/terminal.js" in rels
    assert "jarvis/ui/templates/index.html" in rels


def test_no_double_encoded_text_in_the_shipped_tree():
    offenders = []
    for path in _walk():
        hits, unreadable = scan_file(path)
        if unreadable:
            offenders.append(f"{os.path.relpath(path, ROOT)}: not valid UTF-8")
        for lineno, bad, good in hits:
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            offenders.append(f"{rel}:{lineno}: {bad!r} -> {good!r}")
    assert not offenders, (
        "double-encoded (UTF-8 -> CP1252 -> UTF-8) text found; "
        "run `python tools/audit_encoding.py --fix`:\n  " + "\n  ".join(offenders)
    )
