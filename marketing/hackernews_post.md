# Show HN: Byungeom -- Multi-LLM dialectical code synthesis

**Title (for HN submission):**

> Show HN: Byungeom -- Three LLMs debate your code so bugs don't ship

**URL:** https://github.com/seedrisecenter-maker/byungeom

---

**Body (paste as comment after submission):**

I built byungeom (pronounced "byun-gum," from the Sichuan opera art of instant mask-changing) because I kept hitting the same wall: a single LLM generates confident, well-structured code that silently contains bugs the model is constitutionally blind to.

Claude misses performance traps. GPT overlooks Pythonic idiom. Gemini skips edge cases. Each model's intelligence has a shape, and every shape has a blind side.

**The idea:** apply the Hegelian dialectic (Thesis-Antithesis-Synthesis) to code generation using heterogeneous models.

1. **Thesis (Claude)** -- designs a contract and generates the initial implementation.
2. **Antithesis (GPT)** -- adversarially attacks the code, finds weaknesses, proposes counter-patches.
3. **Synthesis (Gemini)** -- reconciles the conflict. Does not average the two positions; keeps only what survived the attack.

After the dialectic cycle, the code enters the **Star Chamber**: all three models review independently and in parallel. Issues are classified by agreement level (consensus = must fix, majority = should fix, single = worth reviewing). The most conservative verdict wins.

Then it hits **static analysis** (ruff + bandit, concurrent) and **sandboxed execution** (subprocess, 10s TTL, auto-cleanup). Only code classified as ALIVE ships.

**How it differs from existing multi-agent tools:**

- Not a chain-of-thought wrapper. Models actually disagree and attack each other's output.
- Not model-homogeneous. The whole point is that different architectures have different blind spots.
- Star Chamber uses parallel independent review, not sequential contamination. Model B never sees Model A's review before forming its own opinion.
- Graceful degradation: if you only have one API key, it still works -- just with reduced coverage.

**What I've seen in practice:**

- Thread-safe LRU cache: Claude's initial version had a subtle lock-ordering bug. GPT caught it in the antithesis phase. Gemini's synthesis produced a clean reentrant implementation.
- JWT auth API: Star Chamber consensus flagged a timing-attack vulnerability in token comparison that no single model caught alone.
- Concurrency code gets 1-5 debate rounds auto-scaled by complexity.

```bash
pip install byungeom
byungeom generate "implement a thread-safe LRU cache"
```

Python 3.10+, Apache 2.0. Works with any combination of Claude + GPT + Gemini API keys.

Happy to answer questions about the architecture or the philosophy behind adversarial multi-model synthesis.

https://github.com/seedrisecenter-maker/byungeom
