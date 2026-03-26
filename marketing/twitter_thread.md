# Twitter/X Thread for byungeom

---

**Tweet 1 -- Hook**

You ask an LLM to write code. It looks perfect. You ship it.

Then you find the race condition it was 100% confident didn't exist.

The problem isn't that LLMs are bad at code. The problem is that every model has blind spots it can never see.

Here's how I fixed that:

---

**Tweet 2 -- The solution**

I built byungeom -- an open-source tool that makes Claude, GPT, and Gemini *argue* about your code before it ships.

Not chain-of-thought. Not self-review. Actual structured adversarial debate between different model architectures.

One generates. All judge. Only survivors ship.

---

**Tweet 3 -- How it works (the dialectic)**

The protocol follows Hegel's dialectic:

Thesis -- Claude designs a contract and generates code
Antithesis -- GPT attacks it, finds weaknesses, proposes patches
Synthesis -- Gemini reconciles. Doesn't average. Keeps only what survived.

Different training data. Different architectures. Different blind spots.
That's the point.

---

**Tweet 4 -- Star Chamber**

After the dialectic, all three models enter the Star Chamber.

Each reviews the code independently and in parallel. No model sees another's assessment (avoids anchoring bias).

Issues classified by agreement:
- All 3 agree -> must fix
- 2 agree -> should fix
- 1 flags -> worth reviewing

Most conservative verdict wins.

---

**Tweet 5 -- Code**

```bash
pip install byungeom
```

```python
from byungeom import StarChamber

sc = StarChamber()  # auto-detects API keys
result = await sc.review(code, goal)

print(result.consensus_issues)
print(result.worst_verdict)  # ALIVE / WOUNDED / DEAD
```

Then ruff + bandit + sandboxed execution.
Only ALIVE code ships.

---

**Tweet 6 -- What it catches**

Real examples from testing:

- Thread-safe LRU cache: Claude missed a lock-ordering bug. GPT caught it. Gemini synthesized a clean reentrant version.

- JWT auth: Star Chamber consensus flagged a timing-attack vulnerability in token comparison. No single model caught it alone.

Model diversity > model size.

---

**Tweet 7 -- CTA**

byungeom is open source (Apache 2.0).

Python 3.10+. Works with any combination of Claude + GPT + Gemini API keys. Degrades gracefully if you only have one.

GitHub: https://github.com/seedrisecenter-maker/byungeom

Star it if adversarial multi-LLM synthesis sounds like the future of code generation. PRs welcome.
