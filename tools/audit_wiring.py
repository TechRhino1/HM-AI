"""Static + dynamic wiring audit for the JARVIS package.

Two independent passes:

1. **Static import graph.** Parse every module's AST, collect its intra-package
   imports, and report modules nothing imports (candidates for dead code) and
   imports that point at modules which do not exist (broken wiring).

2. **Dynamic import.** Actually import every module and report what raises.
   A module that cannot be imported is a module whose logic path cannot run.

Usage (from the repo root):

    python tools/audit_wiring.py            # summary
    python tools/audit_wiring.py --verbose  # list every module
"""
from __future__ import annotations

import argparse
import ast
import importlib
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PACKAGE = "jarvis"


def module_name(path: str) -> str:
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    rel = rel[:-3] if rel.endswith(".py") else rel
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def all_modules() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, PACKAGE)):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(module_name(os.path.join(dirpath, fn)))
    return sorted(out)


def intra_package_imports(path: str) -> set[str]:
    """Every `jarvis.*` module this file imports, as absolute module names."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(PACKAGE + "."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import — resolve against this file's package.
                pkg = module_name(path)
                if not path.endswith("__init__.py"):
                    pkg = pkg.rsplit(".", 1)[0]
                parts = pkg.split(".") if pkg else []
                base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                target = ".".join(base + ([node.module] if node.module else []))
                found.add(target)
            elif node.module and (
                node.module == PACKAGE or node.module.startswith(PACKAGE + ".")
            ):
                found.add(node.module)
                for alias in node.names:
                    found.add(f"{node.module}.{alias.name}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    modules = all_modules()
    known = set(modules)

    # ── pass 1: static graph ───────────────────────────────────────────────
    imported_by: dict[str, set[str]] = {m: set() for m in modules}
    broken: list[tuple[str, str]] = []

    for mod in modules:
        path = os.path.join(ROOT, mod.replace(".", os.sep) + ".py")
        if not os.path.exists(path):
            path = os.path.join(ROOT, mod.replace(".", os.sep), "__init__.py")
        for target in intra_package_imports(path):
            # A target may be a module or a symbol inside one; keep the longest
            # prefix that is a real module.
            parts = target.split(".")
            resolved = None
            for i in range(len(parts), 0, -1):
                cand = ".".join(parts[:i])
                if cand in known:
                    resolved = cand
                    break
            if resolved:
                imported_by.setdefault(resolved, set()).add(mod)
            elif target.startswith(PACKAGE + ".") and not target.startswith(PACKAGE + "._"):
                broken.append((mod, target))

    print("=" * 78)
    print("STATIC IMPORT GRAPH")
    print("=" * 78)
    print(f"modules parsed      : {len(modules)}")
    print(f"broken import refs  : {len(broken)}")
    for src, tgt in broken:
        print(f"    {src}  ->  {tgt}   [target does not exist]")

    unreferenced = sorted(
        m for m in modules
        if not imported_by.get(m)
        and not m.endswith(".__init__")
        and not m.endswith(".__main__")
    )
    print(f"imported by nothing : {len(unreferenced)}")
    if args.verbose:
        for m in unreferenced:
            print(f"    {m}")

    # ── pass 2: dynamic import ─────────────────────────────────────────────
    print()
    print("=" * 78)
    print("DYNAMIC IMPORT")
    print("=" * 78)

    failures: list[tuple[str, str]] = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except BaseException as exc:  # noqa: BLE001 - a module that cannot import is the finding
            detail = f"{type(exc).__name__}: {exc}"
            failures.append((mod, detail))
            if args.verbose:
                print(f"FAIL  {mod}")
                traceback.print_exc(limit=3)

    print(f"imported OK         : {len(modules) - len(failures)}/{len(modules)}")
    print(f"failed              : {len(failures)}")
    for mod, detail in failures:
        print(f"    FAIL  {mod}")
        print(f"          {detail[:200]}")

    print()
    print("=" * 78)
    print(f"VERDICT: {len(broken)} broken refs, {len(unreferenced)} unreferenced modules, "
          f"{len(failures)} import failures")
    print("=" * 78)
    return 1 if (broken or failures) else 0


if __name__ == "__main__":
    sys.exit(main())
