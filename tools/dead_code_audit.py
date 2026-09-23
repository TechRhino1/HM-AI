"""Dead-code reachability analyzer for the HM-AI project.

Builds the set of modules reachable from every real entry point, then reports
what is NOT reachable. Also scans for *dynamic* references (importlib,
__import__, string literals that name a module) because those are the cases
where a naive reachability walk gives a false "unused" verdict.

Run:  python tools/dead_code_audit.py [--json out.json]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
from collections import deque
from typing import Dict, List, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every real way the system can be started.
ENTRY_POINTS = [
    "main.py",
    "HM_start.py",
    "jarvis.py",
    "run_1year_backtest.py",
    "run_2month_mt5_backtest.py",
    "run_3week_backtest.py",
    "run_6month_backtest.py",
    "run_6month_4symbol.py",
    "run_6month_real_mt5.py",
    "run_80_dollar_account_backtest.py",
    "run_all_symbols_6month_backtest.py",
    "run_comprehensive_1y_mt5_backtest.py",
    "run_fast_6m.py",
    "run_live_multi_asset_sweep.py",
    "run_validation_report.py",
    # CLI entry points and maintenance tools count as live entry points too —
    # they are how a human starts a supported workflow.
    "jarvis/historical/cli.py",
    "tools/fetch_real_data.py",
    "tools/dead_code_audit.py",
]

# Directories that are part of the shipped codebase (candidates for the report).
SCAN_DIRS = ["jarvis", "engines", "core", "strategies", "config", "tools"]

LOCAL_PKGS = {"jarvis", "engines", "core", "strategies", "config", "tools"}


def module_name_for_path(path: str) -> str:
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def path_for_module(mod: str) -> str | None:
    base = mod.replace(".", os.sep)
    for cand in (os.path.join(ROOT, base + ".py"), os.path.join(ROOT, base, "__init__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def _local_deps_from_source(src: str, self_mod: str) -> Set[str]:
    """Top-level modules imported by this file that belong to the project."""
    out: Set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in LOCAL_PKGS:
                    out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import: resolve against the current package.
                parts = self_mod.split(".")
                pkg = parts[: len(parts) - 1]
                if node.level > 1:
                    pkg = pkg[: -(node.level - 1)]
                base = ".".join(pkg + ([node.module] if node.module else []))
                out.add(base)
            elif node.module and node.module.split(".")[0] in LOCAL_PKGS:
                out.add(node.module)
    return out


def dynamic_reference_scan() -> Dict[str, List[Tuple[str, int]]]:
    """Find dynamic imports — a module named only as a string is still 'used'."""
    hits: Dict[str, List[Tuple[str, int]]] = {}
    pat = re.compile(
        r"""importlib\.import_module\(\s*['"]([\w\.]+)['"]|"""
        r"""__import__\(\s*['"]([\w\.]+)['"]|"""
        r"""import_module\(\s*['"]([\w\.]+)['"]"""
    )
    str_pat = re.compile(r"""['"]((?:jarvis|engines|core|strategies|config)(?:\.[\w]+)+)['"]""")
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in
                       {".git", "__pycache__", ".pytest_cache", ".workbuddy-ai", "data", "logs", "venv", ".venv"}]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as _f:
                    src = _f.read()
            except OSError:
                continue
            rel = os.path.relpath(p, ROOT)
            for m in pat.finditer(src):
                mod = next(g for g in m.groups() if g)
                hits.setdefault(mod, []).append((rel, src[: m.start()].count("\n") + 1))
            for m in str_pat.finditer(src):
                mod = m.group(1)
                hits.setdefault(mod, []).append((rel, src[: m.start()].count("\n") + 1))
    return hits


def build_graph() -> Tuple[Set[str], Dict[str, Set[str]]]:
    reachable: Set[str] = set()
    graph: Dict[str, Set[str]] = {}
    q = deque()

    for ep in ENTRY_POINTS:
        p = os.path.join(ROOT, ep)
        if os.path.isfile(p):
            q.append(module_name_for_path(p))

    while q:
        mod = q.popleft()
        if mod in reachable:
            continue
        p = path_for_module(mod)
        if p is None:
            continue
        reachable.add(mod)
        try:
            with open(p, encoding="utf-8", errors="replace") as _f:
                src = _f.read()
        except OSError:
            continue
        deps = _local_deps_from_source(src, mod)
        graph[mod] = deps
        for d in deps:
            # Package roots need their __init__ pulled in too.
            if d not in reachable:
                q.append(d)
    return reachable, graph


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    reachable, graph = build_graph()
    dyn = dynamic_reference_scan()

    # Expand reachability with dynamic references (conservative: treat as used).
    dyn_reachable = set()
    for mod in dyn:
        if mod.split(".")[0] in LOCAL_PKGS:
            dyn_reachable.add(mod)

    all_modules: Set[str] = set()
    file_map: Dict[str, str] = {}
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    p = os.path.join(dirpath, fn)
                    m = module_name_for_path(p)
                    all_modules.add(m)
                    file_map[m] = p

    unreachable = sorted(all_modules - reachable - dyn_reachable)

    # Package __init__ files are pulled in implicitly whenever any submodule is
    # imported, so a bare "jarvis.data.__init__ unreachable" is a walker artefact,
    # not a finding. Treat a package as reachable if any of its children is.
    def _is_pkg_init(m: str) -> bool:
        return m.endswith(".__init__")

    def _pkg_of(m: str) -> str:
        return m[: -len(".__init__")] if _is_pkg_init(m) else ""

    pkg_roots_reachable = set()
    for m in list(reachable) + list(dyn_reachable):
        parts = m.split(".")
        for i in range(1, len(parts)):
            pkg_roots_reachable.add(".".join(parts[:i]))

    filtered: List[str] = []
    for m in unreachable:
        if _is_pkg_init(m) and _pkg_of(m) in pkg_roots_reachable:
            continue          # implicit, not dead
        filtered.append(m)
    unreachable = filtered

    print("=" * 78)
    print("DEAD CODE AUDIT")
    print("=" * 78)
    print(f"entry points scanned : {len(ENTRY_POINTS)}")
    print(f"reachable modules    : {len(reachable)}")
    print(f"dynamically referenced: {len(dyn_reachable)}")
    print(f"total modules in scope: {len(all_modules)}")
    print(f"UNREACHABLE           : {len(unreachable)}")
    print()

    # Group by top-level dir
    groups: Dict[str, List[str]] = {}
    for m in unreachable:
        groups.setdefault(m.split(".")[0], []).append(m)

    print("-" * 78)
    for g in sorted(groups, key=lambda k: -len(groups[k])):
        mods = groups[g]
        print(f"\n[{g}]  {len(mods)} unreachable module(s)")
        for m in mods:
            p = file_map.get(m, "?")
            try:
                with open(p, encoding="utf-8", errors="replace") as _f:
                    n = sum(1 for _ in _f)
            except OSError:
                n = 0
            print(f"    {m:58s} {n:5d} lines")

    # Who imports the unreachable ones? (must be only other unreachable)
    print()
    print("-" * 78)
    print("CROSS-CHECK: is any unreachable module imported by a REACHABLE module?")
    print("(if yes, it is NOT dead — the walk missed something)")
    print("-" * 78)
    suspicious = []
    test_only: List[str] = []
    for target in unreachable:
        importers = [m for m, deps in graph.items() if target in deps]
        test_importers = []
        tdir = os.path.join(ROOT, "tests")
        if os.path.isdir(tdir):
            for fn in os.listdir(tdir):
                if fn.endswith(".py"):
                    try:
                        with open(os.path.join(tdir, fn), encoding="utf-8", errors="replace") as _f:
                            s = _f.read()
                    except OSError:
                        continue
                    if re.search(rf"\b{re.escape(target)}\b", s):
                        test_importers.append(f"tests/{fn}")
        if importers:
            suspicious.append((target, importers, test_importers))
        elif test_importers:
            test_only.append(f"{target}  <- {', '.join(test_importers)}")

    if suspicious:
        for t, imp, ti in suspicious:
            print(f"  !! {t}")
            print(f"      LIVE IMPORTERS : {imp}   <-- NOT DEAD, do not delete")
            if ti:
                print(f"      test refs      : {ti}")
    else:
        print("  none — no unreachable module is imported by a live module")

    print()
    print("-" * 78)
    print(f"TEST-ONLY MODULES ({len(test_only)}) — no production path, but tests reference them")
    print("Deleting these REQUIRES deleting/porting the test too.")
    print("-" * 78)
    for line in test_only:
        print(f"  {line}")

    # Truly dead: unreachable AND no test reference either.
    test_only_mods = {line.split("  <-")[0] for line in test_only}
    truly_dead = [m for m in unreachable if m not in test_only_mods]
    print()
    print("-" * 78)
    print(f"TRULY DEAD ({len(truly_dead)}) — unreachable and referenced by nothing at all")
    print("-" * 78)
    for m in truly_dead:
        print(f"  {m}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "reachable": sorted(reachable),
                "unreachable": unreachable,
                "dynamic": {k: v for k, v in dyn.items()},
                "suspicious": [{"module": t, "live_importers": i, "test_refs": ti}
                               for t, i, ti in suspicious],
            }, f, indent=2)
        print(f"\njson -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
