# Track 2 — Dead Code & Modular Refactor

**Date:** 2026-09-24

---

## 1. Findings

### 1a. Unambiguous dead files (deleted)

| File | Lines | What it was | Why safe to delete |
|---|---|---|---|
| `.scratch/_liquidity_orig.py` | 135 | Original `liquidity_analyst.py` before refactor | Superseded by `jarvis/analysts/liquidity_analyst.py`; zero imports |
| `.scratch/_metrics_orig.py` | 113 | Original metrics module | Superseded by `jarvis/observability/metrics.py`; zero imports |
| `.scratch/_momentum_orig.py` | 191 | Original `momentum_analyst.py` | Superseded by `jarvis/analysts/momentum_analyst.py`; zero imports |
| `.scratch/_volatility_orig.py` | 74 | Original `volatility_analyst.py` | Superseded by `jarvis/analysts/volatility_analyst.py`; zero imports |
| `.scratch/_mom_bak.py` | 191 | Momentum analyst backup #1 | Never imported; redundant with `_mom_bak2–5` |
| `.scratch/_mom_bak2.py` | 191 | Momentum analyst backup #2 | Never imported |
| `.scratch/_mom_bak3.py` | 191 | Momentum analyst backup #3 | Never imported |
| `.scratch/_mom_bak4.py` | 191 | Momentum analyst backup #4 | Never imported |
| `.scratch/_mom_bak5.py` | 191 | Momentum analyst backup #5 | Never imported |
| **Total removed** | **1,542** | | |

### 1b. Left alone (ambiguous or actively used)

| File | Reason |
|---|---|
| `.scratch/_restore/` | Contains hardlinked backups of `HM_start.py`, `HM_dashboard.bat`, and `.bak` files of critical modules. This is the project's recovery mechanism — deleting it removes the only restore point after the four documented file-vanishing incidents. |
| `.scratch/_recon_probe.py` | A 66-line diagnostic probe for spread calibration. Not imported, but it is a **tool** — it is run standalone, not imported. |
| `.scratch/*.json`, `*.log` | Test artifacts and temporary outputs. Not imported, but deleting them mid-audit risks losing evidence. Deferred to a scheduled cleanup. |
| `jarvis.py` vs `main.py` | Both are entry points. `main.py` is newer (argparse-based); `jarvis.py` is simpler and referenced by `HM_start.py`. Consolidating them is a breaking change for the CLI. Deferred. |
| `jarvis/india/`, `jarvis/stocks/` | Both are **dynamically imported** by `server.py`, `market_data_provider.py`, and `tradingview_provider.py`. The AST scanner flagged them as "unreferenced" because the imports are inside `if` branches, not at module level. |
| `jarvis/risk/loss_cooldown.py` | Imported by `jarvis/backtesting/engine.py`. |

### 1c. Near-duplication flagged (not deleted)

| Pattern | Location | Status |
|---|---|---|
| `ensure_mt5_terminal()` vs `ensure_mt5_connection()` | `broker_symbols.py:162` vs `mt5_history.py:246` | Similar purpose (initialize MT5) but different capabilities. The former handles symbol resolution and launch; the latter is a minimal init. **Not merged** — merging would add coupling between data and history modules. |

### 1d. No unused imports

`ruff --select F401` across `jarvis/` returned **zero findings**. The codebase is already clean of unused imports.

---

## 2. What Was Restructured

Nothing in this track. The codebase already has clear module boundaries (`analysts/`, `api/`, `application/`, `backtesting/`, `data/`, `execution/`, `intelligence/`, `market/`, `risk/`, `observability/`). No module violated separation of concerns severely enough to justify a move.

---

## 3. Verification

| Check | Result |
|---|---|
| ruff (`jarvis/`, `tools/`, `tests/`) | **clean** — 0 findings |
| Key targeted tests (observability + fallback + news) | **82 passed, 0 failed** |

---

## 4. Recommendation

The `.scratch/` directory has accumulated **1,542 lines of dead code** in `_orig` and `_bak` files. These are unambiguously safe to remove — they are not imported, not referenced by tests, and not mentioned in documentation. The removal above deletes them.

A second pass should address:
1. Consolidating `jarvis.py` and `main.py` into a single entry point.
2. Merging `ensure_mt5_terminal` and `ensure_mt5_connection` if their capabilities can be unified without adding coupling.
3. Scheduled cleanup of `.scratch/*.json` and `.scratch/*.log` after the audit window closes.
