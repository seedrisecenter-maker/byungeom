# Reddit Posts for byungeom

---

## r/MachineLearning

**Title:** [P] Byungeom: Applying Hegelian dialectics (Thesis-Antithesis-Synthesis) to multi-LLM code generation

**Body:**

I've been exploring a question that sits at the intersection of epistemology and practical ML engineering: **what happens when you formalize philosophical dialectics as a multi-agent LLM protocol?**

The core observation is straightforward. Each large language model has systematic blind spots -- not random failures, but consistent patterns of what it overlooks. Claude tends to miss performance-critical bugs. GPT frequently overlooks idiomatic patterns. Gemini skips edge cases. These aren't flaws in any individual model; they're a consequence of the fact that each architecture's "intelligence has a shape, and every shape has a blind side."

**byungeom** (from the Sichuan opera art of instant mask-changing) formalizes this into a three-phase dialectical protocol:

| Phase | Dialectic | Role | Model |
|-------|-----------|------|-------|
| 1 | Thesis | Architect | Claude designs the contract, generates initial code |
| 2 | Antithesis | Adversary | GPT attacks the code, proposes counter-patches |
| 3 | Synthesis | Judge | Gemini reconciles -- keeps only what survived |

After the dialectic cycle, all models enter the **Star Chamber**: independent, parallel review where no model sees another's assessment before forming its own verdict. This avoids the well-documented anchoring bias problem in sequential multi-agent review.

Issues are classified by inter-model agreement:
- **Consensus** (all models agree) -- must fix
- **Majority** (2+ agree) -- should fix
- **Individual** (1 flagged) -- worth reviewing

The most conservative verdict wins: ALIVE (ship), WOUNDED (patch loop), or DEAD (redesign).

**What makes this different from existing multi-agent frameworks:**

1. **Heterogeneous models by design.** This is not about running the same model three times. The value comes from architectural diversity -- different training data, different RLHF, different failure modes.

2. **Adversarial structure, not cooperative.** The antithesis phase is explicitly destructive. The second model's job is to break the first model's output, not to build on it.

3. **Independence in evaluation.** Star Chamber reviews are parallel and isolated. This is the same principle behind independent jury deliberation -- contamination destroys the information-theoretic value of multiple perspectives.

4. **Verification pipeline.** After LLM consensus, code passes through static analysis (ruff + bandit) and sandboxed execution. The LLMs provide the creative/analytical layer; deterministic tools provide the ground truth.

There's an interesting connection to ensemble methods here, but the mechanism is fundamentally different from model ensembling. Rather than averaging outputs, byungeom uses structured conflict to surface information that no single model would produce.

I'd be curious to hear thoughts from the community on:
- Whether the dialectical framing adds genuine epistemic value beyond simple multi-model voting
- Optimal model pairings for maximizing blind-spot coverage
- How this approach might extend beyond code generation to other structured reasoning tasks

**Repo:** https://github.com/seedrisecenter-maker/byungeom
**License:** Apache 2.0 | **Python 3.10+** | **pip install byungeom**

---

---

## r/Python

**Title:** I built a tool that makes 3 different LLMs argue about your code before it ships. Here's how to use it.

**Body:**

**The problem:** You ask an LLM to write code. It looks good. You ship it. Then you discover the race condition / SQL injection / off-by-one error that the model was confident didn't exist.

**The fix:** Make three models fight about it first.

**byungeom** pits Claude, GPT, and Gemini against each other in a structured debate (Thesis -> Antithesis -> Synthesis), then runs parallel independent review, static analysis, and sandboxed execution.

### Install

```bash
# Core
pip install byungeom

# With Gemini support
pip install byungeom[gemini]

# With static analysis (ruff + bandit)
pip install byungeom[analysis]

# Everything
pip install byungeom[all]
```

Requires Python 3.10+ and Claude CLI (`npm install -g @anthropic-ai/claude-cli`).

### CLI usage

```bash
# One-liner: generate and verify
byungeom generate "implement a thread-safe LRU cache"

# Full pipeline with all features
python orchestrator_v50.py "JWT authentication API" --all-upgrades

# Control debate rounds
python orchestrator_v50.py "quicksort" --loops 5
```

### Python API

```python
import asyncio
from byungeom import StarChamber, Verifier, safe_execute

async def main():
    # Star Chamber: all models review independently
    sc = StarChamber()  # auto-detects your API keys
    result = await sc.review(code=my_code, goal="thread-safe LRU cache")

    print(result.consensus_issues)   # all 3 models agree: must fix
    print(result.majority_issues)    # 2+ models agree: should fix
    print(result.worst_verdict)      # ALIVE / WOUNDED / DEAD

    # Verifier: single-model critique + static + execution
    v = Verifier(model="sonnet", deep=True)
    vr = await v.verify(code, "LRU cache", run_static=True, run_exec=True)
    print(vr.verdict, vr.severity)   # severity: 1-5

    # Standalone sandboxed execution
    er = await safe_execute(code, timeout=10)
    print(er.stdout, er.success, er.timed_out)

asyncio.run(main())
```

### What you get

```
workspace/
  assembly/    # per-loop results
  best.py      # highest-verdict code
  memory.db    # SQLite attack memory (no repeated critiques)
  pipeline.md  # full execution log
```

### Key features

- **Star Chamber** -- parallel multi-model consensus review. No model sees another's review (avoids anchoring).
- **Static analysis** -- ruff + bandit run concurrently, return structured `Issue` objects.
- **Safe execution** -- sandboxed subprocess, configurable timeout, auto-cleans temp files.
- **Graceful degradation** -- only have one API key? It still works, just with fewer reviewers.
- **Attack memory** -- SQLite-backed, so the same critique never repeats across sessions.

### Auto-scaled complexity

| Complexity | Timeout | Example |
|-----------|---------|---------|
| LOW | 90s | Simple functions |
| MID | 120s | Classes, algorithms |
| HIGH | 180s | Concurrency, security |

Apache 2.0. Contributions welcome -- especially new LLM backends (Mistral, Cohere) and CI integration.

**GitHub:** https://github.com/seedrisecenter-maker/byungeom
