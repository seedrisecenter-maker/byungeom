# byungeom (變臉)

> **Multi-face code synthesis — where LLMs debate so your code doesn't break**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/byungeom?color=orange)](https://pypi.org/project/byungeom/)
[![Stars](https://img.shields.io/github/stars/seedrisecenter-maker/byungeom?style=social)](https://github.com/seedrisecenter-maker/byungeom)

---

## Why byungeom?

Every LLM has a blind spot.

- **Claude** misses performance bugs
- **GPT** overlooks Pythonic idiom
- **Gemini** skips edge cases

**Together, they catch everything.**

byungeom is the world's first **heterogeneous-LLM dialectical code synthesis tool**. Instead of asking one model to generate and self-review code, it forces three adversarial reviewers to attack the same code from independent angles — then reconciles their verdicts into a single, hardened result.

```
One model generates. All models judge. Only survivors ship.
```

---

## How It Works

byungeom applies the classical dialectic (正反合 / Thesis-Antithesis-Synthesis) to code generation:

```
  ┌─────────────────────────────────────────────────────────────┐
  │                  byungeom pipeline                          │
  │                                                             │
  │   正 (Thesis)          反 (Antithesis)      合 (Synthesis)  │
  │   ─────────────        ──────────────────   ─────────────── │
  │   Claude generates  →  GPT attacks &     →  Gemini          │
  │   initial code         counter-proposes     reconciles      │
  │                        patch                                │
  │                                                             │
  │                         ▼                                   │
  │              ┌──────────────────────┐                       │
  │              │   Star Chamber       │                       │
  │              │  (Parallel Review)   │                       │
  │              │  Claude + GPT +      │                       │
  │              │  Gemini all vote     │                       │
  │              └──────────┬───────────┘                       │
  │                         │                                   │
  │                         ▼                                   │
  │              ┌──────────────────────┐                       │
  │              │  Static Analysis     │                       │
  │              │  ruff + bandit       │                       │
  │              └──────────┬───────────┘                       │
  │                         │                                   │
  │                         ▼                                   │
  │              ┌──────────────────────┐                       │
  │              │  Safe Execution      │                       │
  │              │  Sandboxed, 10s TTL  │                       │
  │              └──────────┬───────────┘                       │
  │                         │                                   │
  │              DEAD ◄─────┼────► WOUNDED ──► patch loop       │
  │                         │                                   │
  │                      ALIVE                                  │
  │                      (ship it)                              │
  └─────────────────────────────────────────────────────────────┘
```

| Phase | Role | Model | Action |
|-------|------|-------|--------|
| 正 (Thesis) | Architect | Claude | Designs contract + generates code |
| 反 (Antithesis) | Implementer | GPT | Attacks weaknesses, counter-proposes patches |
| 合 (Synthesis) | Judge | Gemini | Reconciles, assigns ALIVE / WOUNDED / DEAD |
| Star Chamber | All Jurors | Claude + GPT + Gemini | Independent parallel review, consensus classification |
| Verify | Guard | ruff + bandit + subprocess | Static analysis + safe execution |

---

## Quick Start

```bash
pip install byungeom
```

```python
import asyncio
from byungeom import StarChamber

async def main():
    sc = StarChamber()
    result = await sc.review(
        code="def lru_cache(maxsize=128): ...",
        goal="implement a thread-safe LRU cache"
    )
    print(result.worst_verdict)

asyncio.run(main())
```

byungeom auto-detects which API keys you have set and activates the corresponding reviewers.

---

## Features

### Star Chamber: Multi-Model Consensus Review

All available models review the same code **independently and in parallel**. Issues are classified by agreement level:

```python
from byungeom import StarChamber

sc = StarChamber()  # auto-detects Claude, GPT, Gemini
result = await sc.review(code, goal)

print(result.consensus_issues)     # ALL models agree → must fix
print(result.majority_issues)      # 2+ models agree → should fix
print(result.individual_issues)    # 1 model flagged → worth reviewing
print(result.worst_verdict)        # most conservative verdict wins
print(result.participating_models) # which models were available
```

Verdicts: `ALIVE` (ship it) | `WOUNDED` (patch loop) | `DEAD` (redesign from scratch)

### Static Analysis: ruff + bandit

Runs ruff and bandit **concurrently** on a temp file, returns structured `Issue` objects:

```python
from byungeom import run_ruff, run_bandit

ruff_result  = await run_ruff(code)   # style, unused imports, complexity
bandit_result = await run_bandit(code) # security: B101, B102, B324...

for issue in ruff_result.issues:
    print(f"[{issue.tool}] L{issue.line} {issue.code}: {issue.message}")
```

Both tools gracefully skip when not installed — no crash, no noise.

### Safe Execution: Sandboxed Subprocess

Executes code in an isolated subprocess with a configurable timeout. Always cleans up the temp file:

```python
from byungeom import safe_execute

result = await safe_execute(code, timeout=10)
print(result.stdout)
print(result.success)    # True if returncode == 0
print(result.timed_out)  # True if killed by timeout
```

Automatically strips fenced code blocks (` ```python ... ``` `) before execution.

### Verifier: Full 正反合 Cycle

Combines 合 (Claude-based critique) + static analysis + execution in one call:

```python
from byungeom import Verifier

v = Verifier(model="sonnet", deep=True)  # deep=True → aggressive security probe
result = await v.verify(
    code="def add(a, b): return a + b",
    goal="add two numbers",
    run_static=True,
    run_exec=True,
)

print(result.verdict)       # ALIVE / WOUNDED / DEAD
print(result.attacks)       # list of specific attack strings
print(result.static_issues) # ruff + bandit findings
print(result.exec_result)   # ExecResult with stdout/stderr
print(result.severity)      # 1–5 severity score
```

### Configurable Debate Rounds

The orchestrator runs 1–5 debate loops based on estimated code complexity:

| Complexity | Timeout | Description |
|-----------|---------|-------------|
| LOW | 90s | Simple functions, utilities |
| MID | 120s | Classes, algorithms |
| HIGH | 180s | Concurrency, security-sensitive |

---

## Installation

```bash
# Minimal (Star Chamber + Verifier, no optional tools)
pip install byungeom

# With Gemini support
pip install byungeom[gemini]

# With static analysis (ruff + bandit)
pip install byungeom[analysis]

# Everything
pip install byungeom[all]
```

**Requirements:** Python 3.10+, Claude CLI (`npm install -g @anthropic-ai/claude-cli`)

---

## Usage

### CLI

```bash
# With all v5.0 features enabled
python orchestrator_v50.py "JWT authentication API" --all-upgrades

# Enable specific features
python orchestrator_v50.py "web scraper" --gemini --static-analysis --execute

# Control debate rounds
python orchestrator_v50.py "quicksort" --loops 5

# Use a specific Claude model
python orchestrator_v50.py "parser" --model opus

# Consecutive mode (exit on 2/3 recent ALIVE verdicts)
python orchestrator_v50.py "binary tree" --consecutive
```

Output artifacts:

```
workspace/
  assembly/    # per-loop results
  best.py      # highest-verdict code across all loops
  memory.db    # SQLite goal-hash memory (avoids repeating attacks)
  pipeline.md  # execution log
```

### Python API

```python
import asyncio
from byungeom import StarChamber, Verifier, safe_execute

async def main():
    # --- Star Chamber: parallel multi-model review ---
    sc = StarChamber(
        claude_model="sonnet",
        include_gemini=True,
        include_gpt=True,
        timeout=120,
    )
    consensus = await sc.review(code=my_code, goal="thread-safe LRU cache")

    if consensus.worst_verdict == "DEAD":
        print("Redesign needed:", consensus.consensus_issues)
    elif consensus.worst_verdict == "WOUNDED":
        print("Patch required:", consensus.majority_issues)
    else:
        print("Code approved by Star Chamber")

    # --- Verifier: single-model 正反合 cycle ---
    v = Verifier(model="sonnet", deep=False)
    result = await v.verify(my_code, "thread-safe LRU cache",
                            run_static=True, run_exec=True)
    print(result.verdict, result.severity)

    # --- Safe execution standalone ---
    exec_result = await safe_execute(my_code, timeout=10)
    print(exec_result.stdout)

asyncio.run(main())
```

---

## Architecture

```
byungeom/
  __init__.py         # public API surface
  star_chamber.py     # StarChamber: parallel multi-model consensus review
  verifier.py         # Verifier: single-cycle 正反合 critic (Claude CLI)
  static_analysis.py  # run_ruff(), run_bandit() — async, temp-file based
  executor.py         # safe_execute() — sandboxed subprocess with timeout
  pyproject.toml      # package metadata, optional deps

orchestrator_v50.py   # full debate loop CLI (正反合 x N rounds)
```

### Data Flow

```
goal (str)
  │
  ▼
正 Claude ──generates──► code (str)
                              │
              ┌───────────────┼───────────────────┐
              ▼               ▼                   ▼
        Claude             Gemini               GPT
        review             review               review
              │               │                   │
              └───────────────┼───────────────────┘
                              ▼
                     ConsensusResult
                    (consensus / majority / individual issues)
                              │
                    ┌─────────┼──────────┐
                    ▼         ▼          ▼
                  ruff      bandit  safe_execute
                    └─────────┼──────────┘
                              ▼
                        VerifyResult
                     ALIVE / WOUNDED / DEAD
```

---

## Configuration

Set environment variables before running:

```bash
# Required: Claude (used by Verifier and Star Chamber)
# Install Claude CLI: npm install -g @anthropic-ai/claude-cli
# Then: claude login

# Optional: Gemini (activates third juror in Star Chamber)
export GOOGLE_API_KEY=your_google_api_key
# or
export GEMINI_API_KEY=your_gemini_api_key

# Optional: OpenAI GPT (activates second juror in Star Chamber)
export OPENAI_API_KEY=your_openai_api_key
```

byungeom automatically detects which keys are present. Missing models are silently skipped — Star Chamber degrades gracefully to single-model mode.

### StarChamber constructor options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `claude_model` | `"sonnet"` | Claude model slug: `haiku`, `sonnet`, `opus` |
| `include_gemini` | `True` | Attempt Gemini reviews (requires `GOOGLE_API_KEY`) |
| `include_gpt` | `True` | Attempt GPT reviews (requires `OPENAI_API_KEY`) |
| `timeout` | `120` | Per-model call timeout in seconds |

### Verifier constructor options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `model` | `"sonnet"` | Claude model slug |
| `timeout` | `120` | Seconds to wait for Claude CLI |
| `deep` | `False` | Use aggressive security-focused critique prompt |

---

## Contributing

Contributions are welcome. Please follow these steps:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Keep changes focused — one concern per PR
4. Ensure `ruff check` and `bandit` pass on your code
5. Submit a pull request with a clear description

**Good first issues:**
- Add support for additional LLM backends (Mistral, Cohere, etc.)
- Implement a `byungeom generate` CLI entry point
- Add a structured JSON output mode for CI integration
- Write integration tests with mock subprocess calls

---

## Philosophy

### 正反合 (Zheng-Fan-He)

The Hegelian dialectic — Thesis, Antithesis, Synthesis — is not just philosophy. It is the oldest known algorithm for reaching truth through structured conflict.

**正 (Thesis)**: A model proposes. It is confident, well-structured, and incomplete.

**反 (Antithesis)**: A different model attacks. It finds the edge cases, the race conditions, the security holes the proposer was blind to. It does not praise. It destroys.

**合 (Synthesis)**: A third model reconciles. It does not average the two positions. It finds what survives the conflict — the code that is stronger for having been attacked.

One model alone will miss things. Not because it is unintelligent, but because **intelligence has a shape**, and every shape has a blind side. byungeom's Star Chamber ensures that no single model's blind spot becomes your production bug.

> **Code that survives the Star Chamber is code worth shipping.**

---

## License

[Apache 2.0](LICENSE) — use freely, modify openly, attribute honestly.

---

<div align="center">
  <sub>變臉 (byungeom) — "changing faces" — a Sichuan opera art of instantaneous mask transformation.<br>Here, each face is a different model. The art is in the synthesis.</sub>
</div>
