# The HM-AI Agent Coding System

**Companion to:** `MASTER_PLAN.md`
**Purpose:** a repeatable, expert-level multi-agent system for changing this codebase safely.

This repo trades real money. A plausible-looking change that is wrong is worse than no change.
Every rule below exists because its absence already cost this project something.

---

## 1. Roster

Five roles. A role is a *charter*, not a persona — the same model may fill several, but the
charters stay separate and the handoff artifacts stay explicit.

### 1.1 Lead (orchestrator)
Owns the plan, the sequencing, and the final call.
- Decomposes a request into charters; assigns one owner per finding.
- **Re-verifies every agent finding before it enters the plan.** An agent's report describes what
  it *intended*, not necessarily what is true.
- Resolves conflicts between agents (e.g. architect vs. data specialist on where a fix belongs).
- Owns the merge, the commit, and the push.
- **Must not** implement while also reviewing. If the lead writes code, another agent reviews it.

### 1.2 Full-stack architect
Owns topology, boundaries, and the request/execution path.
- Layering: is a change in the right layer? Does it create a cycle?
- Concurrency model, process/thread structure, locking.
- API contracts (request/response shape, versioning, stability).
- Frontend structure and cross-page duplication.
- Scalability ceilings and where they come from.

### 1.3 Data specialist
Owns persistence, provenance, and pipelines.
- Schema, migrations, and `user_version`.
- **Provenance**: can a caller tell real data from a fallback? If not, that is a defect.
- Pipelines: bar/candle correctness, timezone, staleness, join keys.
- Retention, growth, backup/checkpoint semantics.
- Whether a column is actually *read*, or only written.

### 1.4 AI engineer
Owns signal, models, labels, and evaluation honesty.
- Feature correctness: look-ahead leakage, train/serve skew.
- Label definitions — a column that is always 0 is not a label.
- Whether a model is **loaded**, or silently skipped.
- Backtest validity: hermeticity, walk-forward, costs, survivorship.
- Metric honesty: effective sample size, overlapping-trade inflation, annualisation.

### 1.5 Python expert
Owns language-level correctness and the safety net.
- Threading: races, deadlocks, lock contention, TOCTOU, thread leaks.
- Error handling: is a failure swallowed on the money path?
- Dependencies, packaging, import cost.
- Test quality: are the tests real, hermetic, and do they cover the money path?

### 1.6 Verifier (may be the Lead for small changes; must be independent for P0/P1)
Runs the Definition of Done gate in §4 against a change and can block delivery.

---

## 2. Workflow

Six phases. No phase starts before the previous one's artifact exists.

```
CHARTER → INVESTIGATE → PROPOSE → VERIFY → IMPLEMENT → HANDOFF
```

### Phase 1 — Charter
The lead writes a bounded brief: **domain, out-of-scope, environment constraints, output format,
and a size limit.** A charter without an out-of-scope list produces overlapping work.
Every charter must restate the environment's hard rules (see §5).

### Phase 2 — Investigate
Agents work **read-only**. They may write scratch files under `.scratch/` only.
They must cite `file:line` or a measurement for every claim.
Agents do **not** edit tracked files in this phase.

### Phase 3 — Propose
Findings are returned as: `ID | title | impact | effort | evidence | why it matters | fix`.
Impact and effort are rated independently — a high-impact one-liner outranks a high-impact
rewrite. Any finding that recommends *enabling* or *activating* something must state what happens
if the activated component is wrong.

### Phase 4 — Verify (the lead)
The lead spot-checks the highest-impact claims **by reproducing them**. Specifically:
- Re-read the cited code. Does it say what the agent said it says?
- Check for a nearby comment explaining the behaviour is intentional.
- If the fix changes live behaviour, ask: *what is the failure mode if this is wrong?*

> **Case in point (2026-09-20).** The AI engineer reported the `MetaLabeler` model was "trained but
> never loaded" and rated it High, recommending the load path be corrected. Re-reading
> `meta_labeler.py:184-203` showed the gate is **deliberately** inert, with measured evidence
> (test AUC 0.481; top-decile selection *lowered* the win rate from 0.359 to 0.341). Enabling it
> would have activated a harmful veto on a live account. The finding was rejected, and the real
> residual issue — the safe state depended on a wrong path — was fixed by making the intent
> explicit instead. **This is the single most valuable step in the workflow.**

### Phase 5 — Implement
Smallest change that fixes the root cause. No opportunistic refactoring in the same commit.
Every fix ships with a regression test (§3.2).

### Phase 6 — Handoff
The implementer hands the verifier: the diff, the new tests, and a one-line statement of the
behaviour change (or an explicit "no behaviour change"). The verifier runs §4.

---

## 3. Standards

### 3.1 Code
- Fix the **root cause**, not the symptom. If `closed_at` looks wrong, read the UPDATE before
  editing `closed_at`.
- **Do not hardcode a constant the codebase already owns.** A clamp of `1.50` next to a configured
  `0.5` is a bug, even if `1.50` is currently the safer number.
- Preserve deliberate design. If a comment explains why something is odd, believe it and say so.
- **Never** widen an `except` to silence a new error. If a handler must stay broad, log it.
  A bare `except: pass` here hid a typo'd attribute for the entire life of a feature.
- Concurrency: any check-then-act on shared state must be atomic. Hold the lock across
  check *and* claim, or re-validate at claim time.
- Prefer making a failure **visible** over making it quiet. A disabled component must say so.

### 3.2 Tests
- **A regression test must fail against the old code.** Prove it — reproduce the defect with the
  fix reverted, then confirm the fix flips it. A green test that never could have failed proves
  nothing. (Done for P1 this session: pre-fix logic returned `FALLBACK` for new work; post-fix
  returns `OK`.)
- Tests must be **hermetic**: no reads from production `data/`, no writes into it, no network.
  A test that skips cleanly when a fixture is absent is a test that silently disappears.
- Tests must not depend on wall-clock sleeps to pass. Poll a condition with a deadline.
- Clean up threads, pools, and temp state in `tearDown`.
- Cover the **money path** first: sizing, ordering, closing, and anything that can double a
  position.

### 3.3 Documentation
- Every non-obvious constant or deliberately-inert code path gets a comment stating **why**,
  with the measurement that justifies it.
- Findings are recorded in `.workbuddy-ai/memory/` — `TRAPS.md` for reusable traps, dated logs for
  session detail, `MEMORY.md` for the index (hard-truncated; keep it terse).
- The master plan is updated when a milestone closes, and rejected findings are recorded **with
  the reason** so they are not re-derived.

---

## 4. Definition of Done (verifier's gate)

A change is not delivered until **all** of these hold:

1. **Root cause addressed** — not the symptom.
2. **Behaviour change stated explicitly**, or declared "none".
3. **Regression test present, and proven to fail without the fix.**
4. **Targeted tests green**; full suite shows no new failures (baseline: 2531 tests, 3 known
   weekend-only failures in `test_market_data_independence.py`).
5. **No new silently-swallowed exception** on the money path.
6. **No new hardcoded constant** duplicating a configured value.
7. **Deliberate design preserved** — or the comment that documented it was updated too.
8. **Memory updated** if a reusable trap was learned.
9. **Committed and pushed**, and the push verified with `git ls-remote` — never by the push
   message or the exit code.

If any item fails, the verifier blocks and returns the change with the specific item named.

---

## 5. Environment contract (restated in every charter)

- **`.git/` is fragile.** Never `git stash`, `git checkout --`, `git reset`, `git clean`. Commit
  and push early. Verify pushes with `git ls-remote origin refs/heads/main` vs `git rev-parse HEAD`.
- Push needs the tracked wrapper (the helper path contains a space):
  `git -c credential.helper= -c credential.helper='!tools/gcm_wrap.sh' push origin main`
- Writes outside the project directory are refused. Bash, not PowerShell.
- Python: `C:/Users/Itrai/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe`.
- SQLite: open read-only — `sqlite3.connect("file:PATH?mode=ro", uri=True)`.
- Server binds 127.0.0.1 only; local probes need `curl --noproxy '*'`.
- **The live server does not hot-reload.** Python changes need a restart.
- Never leave the platform running only as a session background task.

---

## 6. Anti-patterns (learned here, at cost)

| Anti-pattern | Why it hurts |
|---|---|
| Trusting an agent's summary | It reports intent, not fact. The `MetaLabeler` recommendation was confidently wrong. |
| "Fixing" a sentinel you did not identify | `0.0` means "unset" for `sl`/`tp` and "not closed" for `pnl`. Fixing the wrong one inflates a finding 5×. |
| Computing a metric on rows that cannot support it | Risk on synthetic rows: 150 reported breaches → 27 real. |
| One root cause, many findings | Four "duplicate DB" entries hid a single path-resolution defect. |
| A test that can never fail | Green is not evidence. Prove it fails first. |
| Enabling a component because it is present | A trained model with AUC 0.48 is worse than no model. |
| Measuring without checking the instrument | A selector or path that matches nothing passes vacuously. Assert it found something. |

---

## 7. Escalation

An agent must stop and escalate rather than guess when:
- The fix would change live trading behaviour and its failure mode is unbounded.
- Two agents disagree on ownership of a root cause.
- The correct value of a constant is not discoverable from the repo.
- A change touches the drawdown or circuit-breaker baseline (a stale value there can resurrect a
  poisoned `peak_equity`).
