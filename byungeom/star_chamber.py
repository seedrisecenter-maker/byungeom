"""
Multi-model code review using the Star Chamber pattern (Mozilla AI).

All available models review the same code independently and in parallel.
Issues are then classified by how many reviewers agreed on them.

Usage:
    from byungeom import StarChamber

    sc = StarChamber()  # auto-detects available models
    result = await sc.review(code, goal)
    print(result.consensus_issues)   # all models agree
    print(result.majority_issues)    # 2+ models agree
    print(result.individual_issues)  # only 1 model flagged
    print(result.worst_verdict)      # most conservative verdict
"""

import asyncio
import os
import random
from dataclasses import dataclass, field

# ── Model backend constants ───────────────────────────────────────────────────

_CLAUDE_MODELS = ("haiku", "sonnet")   # tried in order; first available wins
_GEMINI_MODEL = "gemini-2.5-flash"
_GPT_MODEL = "gpt-4o-mini"

_VERDICT_RANK = {"DEAD": 0, "WOUNDED": 1, "ALIVE": 2, "UNKNOWN": 3}

_HAP_SYS = (
    "[역할] 변검 合(합·검증자). 칭찬 금지. 공격만. "
    "ALIVE/WOUNDED/DEAD 판정. 태그만 출력.\n"
)

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class ModelReview:
    """Raw review result from a single model."""

    model_name: str
    verdict: str          # ALIVE / WOUNDED / DEAD / UNKNOWN
    attacks: list[str] = field(default_factory=list)
    feedback: str = ""
    severity: int = 3
    error: str = ""       # non-empty when the call failed


@dataclass
class ConsensusResult:
    """Aggregated Star Chamber review across all participating models."""

    reviews: list[ModelReview] = field(default_factory=list)
    consensus_issues: list[str] = field(default_factory=list)   # all agree
    majority_issues: list[str] = field(default_factory=list)    # 2+ agree
    individual_issues: list[str] = field(default_factory=list)  # only 1 flagged
    worst_verdict: str = "UNKNOWN"    # most conservative among all models
    participating_models: list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_tag(text: str, tag: str) -> str:
    s = text.find(f"[{tag}]")
    e = text.find(f"[/{tag}]")
    return text[s + len(tag) + 2 : e].strip() if s >= 0 and e > s else ""


def _parse_verdict(text: str) -> str:
    upper_words = text.upper().split()
    if "ALIVE" in upper_words:
        return "ALIVE"
    if "DEAD" in upper_words:
        return "DEAD"
    raw = text.upper()
    if "ALIVE" in raw:
        return "ALIVE"
    if raw.rstrip(".,!?;: ").endswith("DEAD"):
        return "DEAD"
    return "WOUNDED"


def _parse_severity(text: str) -> int:
    try:
        return max(1, min(5, int(text.strip()[:1])))
    except (ValueError, IndexError):
        return 3


def _build_hap_prompt(code: str, goal: str) -> str:
    parts = [
        f"목표:{goal[:80]}",
        f"코드:\n{code[:1500]}",
        "[V]ALIVE/WOUNDED/DEAD[/V]\n[A]공격[/A]\n[F]정(正)방향수정[/F]\n[S]1-5심각도[/S]",
    ]
    return "\n".join(parts)


def _worst_verdict(verdicts: list[str]) -> str:
    """Return the most conservative (lowest-ranked) verdict."""
    if not verdicts:
        return "UNKNOWN"
    return min(verdicts, key=lambda v: _VERDICT_RANK.get(v, 99))


def _classify_issues(reviews: list[ModelReview]) -> tuple[list[str], list[str], list[str]]:
    """
    Classify attack strings by agreement level across reviewers.

    Simple keyword-overlap approach: two attacks are considered "the same"
    if they share at least 2 significant words (length >= 4).

    Returns:
        (consensus_issues, majority_issues, individual_issues)
    """
    if not reviews:
        return [], [], []

    n = len(reviews)
    all_attacks: list[tuple[int, str]] = []  # (reviewer_index, attack)
    for i, r in enumerate(reviews):
        for a in r.attacks:
            all_attacks.append((i, a))

    if not all_attacks:
        return [], [], []

    def _sig_words(text: str) -> set[str]:
        return {w.lower() for w in text.split() if len(w) >= 4}

    # Cluster attacks greedily
    clusters: list[tuple[set[int], str]] = []  # (reviewer_set, representative)
    used: set[int] = set()

    for idx, (rev_i, attack) in enumerate(all_attacks):
        if idx in used:
            continue
        sig = _sig_words(attack)
        cluster_reviewers: set[int] = {rev_i}
        for jdx, (rev_j, other) in enumerate(all_attacks):
            if jdx <= idx or jdx in used:
                continue
            other_sig = _sig_words(other)
            if sig & other_sig and len(sig & other_sig) >= min(2, len(sig), len(other_sig)):
                cluster_reviewers.add(rev_j)
                used.add(jdx)
        used.add(idx)
        clusters.append((cluster_reviewers, attack))

    consensus: list[str] = []
    majority: list[str] = []
    individual: list[str] = []

    for reviewers, rep in clusters:
        cnt = len(reviewers)
        if n >= 2 and cnt >= n:
            consensus.append(rep)
        elif cnt >= 2:
            majority.append(rep)
        else:
            individual.append(rep)

    return consensus, majority, individual


# ── Backend callers ───────────────────────────────────────────────────────────


async def _call_claude(
    model: str, full_prompt: str, timeout: float = 120
) -> str:
    """Call ``claude -p`` subprocess."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "text",
        "--no-chrome",
    ]
    for attempt in range(2):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(full_prompt.encode("utf-8")), timeout=timeout
            )
            out = stdout_b.decode("utf-8", errors="replace").strip()
            if proc.returncode == 0 and out:
                return out
            if attempt == 0:
                await asyncio.sleep(1 + random.uniform(0, 0.5))
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return f"[오류] 타임아웃"
        except FileNotFoundError:
            return "[오류] claude CLI 없음"
        except Exception as exc:
            return f"[오류] {exc}"
    return "[오류] 빈 응답"


async def _call_gemini(full_prompt: str, timeout: float = 60) -> str:
    """Call Gemini via google-genai SDK (new) or google-generativeai (legacy)."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "[오류] GEMINI_API_KEY 환경변수 없음"

    # Try new SDK first, fall back to legacy
    try:
        from google import genai  # type: ignore[import-untyped]

        client = genai.Client(api_key=api_key)
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=_GEMINI_MODEL, contents=full_prompt
                ),
            ),
            timeout=timeout,
        )
        return response.text or "[오류] 빈 응답"
    except ImportError:
        pass
    except asyncio.TimeoutError:
        return f"[오류] Gemini 타임아웃({timeout}s)"
    except Exception as exc:
        return f"[오류] Gemini: {exc}"

    # Legacy SDK fallback
    try:
        import google.generativeai as genai_legacy  # type: ignore[import-untyped]

        genai_legacy.configure(api_key=api_key)
        model = genai_legacy.GenerativeModel(_GEMINI_MODEL)
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: model.generate_content(full_prompt)),
            timeout=timeout,
        )
        return response.text or "[오류] 빈 응답"
    except ImportError:
        return "[오류] google-genai 미설치"
    except asyncio.TimeoutError:
        return f"[오류] Gemini 타임아웃({timeout}s)"
    except Exception as exc:
        return f"[오류] Gemini: {exc}"


async def _call_gpt(full_prompt: str, timeout: float = 60) -> str:
    """Call GPT via OpenAI SDK."""
    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        return "[오류] openai 미설치"

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "[오류] OPENAI_API_KEY 환경변수 없음"

    try:
        client = OpenAI(api_key=api_key)
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=_GPT_MODEL,
                    messages=[
                        {"role": "system", "content": _HAP_SYS},
                        {"role": "user", "content": full_prompt},
                    ],
                    max_tokens=1024,
                ),
            ),
            timeout=timeout,
        )
        return response.choices[0].message.content or "[오류] 빈 응답"
    except asyncio.TimeoutError:
        return f"[오류] GPT 타임아웃({timeout}s)"
    except Exception as exc:
        return f"[오류] GPT: {exc}"


def _parse_review(model_name: str, raw: str) -> ModelReview:
    """Parse a raw 합(合) response into a ModelReview."""
    if raw.startswith("[오류]"):
        return ModelReview(model_name=model_name, verdict="UNKNOWN", error=raw)

    v_raw = _extract_tag(raw, "V")
    attack_raw = _extract_tag(raw, "A")
    feedback = _extract_tag(raw, "F")
    severity = _parse_severity(_extract_tag(raw, "S"))
    verdict = _parse_verdict(v_raw or raw)

    attacks = [a.strip() for a in attack_raw.splitlines() if a.strip()]
    if not attacks and attack_raw.strip():
        attacks = [attack_raw.strip()]

    return ModelReview(
        model_name=model_name,
        verdict=verdict,
        attacks=attacks,
        feedback=feedback,
        severity=severity,
    )


# ── StarChamber ───────────────────────────────────────────────────────────────


class StarChamber:
    """
    Multi-model parallel code reviewer.

    On construction, probes which backends are available (claude CLI, Gemini, GPT).
    Skips unavailable backends silently — if none are available, review()
    returns an empty ConsensusResult.
    """

    def __init__(
        self,
        claude_model: str = "sonnet",
        include_gemini: bool = True,
        include_gpt: bool = True,
        timeout: float = 120,
    ) -> None:
        """
        Args:
            claude_model:   Claude model slug used for Claude reviews.
            include_gemini: Whether to attempt Gemini reviews (requires SDK + API key).
            include_gpt:    Whether to attempt GPT reviews (requires openai SDK + API key).
            timeout:        Per-model call timeout in seconds.
        """
        self.claude_model = claude_model
        self.include_gemini = include_gemini
        self.include_gpt = include_gpt
        self.timeout = timeout

    async def review(self, code: str, goal: str) -> ConsensusResult:
        """
        Run all available models against the code concurrently.

        Args:
            code: Python source code to review.
            goal: Natural-language description of intent.

        Returns:
            ConsensusResult classifying issues by agreement level.
        """
        prompt = _build_hap_prompt(code, goal)
        full_prompt = f"{_HAP_SYS}\n---\n{prompt}"

        # Build task list: one per model
        tasks: list[tuple[str, asyncio.coroutine]] = []
        tasks.append((
            f"claude-{self.claude_model}",
            _call_claude(self.claude_model, full_prompt, self.timeout),
        ))
        if self.include_gemini:
            tasks.append((
                _GEMINI_MODEL,
                _call_gemini(full_prompt, self.timeout),
            ))
        if self.include_gpt:
            tasks.append((
                _GPT_MODEL,
                _call_gpt(full_prompt, self.timeout),
            ))

        raw_results = await asyncio.gather(
            *[coro for _, coro in tasks], return_exceptions=True
        )

        reviews: list[ModelReview] = []
        for (name, _), raw in zip(tasks, raw_results):
            if isinstance(raw, Exception):
                reviews.append(ModelReview(model_name=name, verdict="UNKNOWN", error=str(raw)))
            else:
                reviews.append(_parse_review(name, str(raw)))

        # Filter out fully failed reviews for classification
        valid = [r for r in reviews if r.verdict != "UNKNOWN"]
        consensus, majority, individual = _classify_issues(valid)

        verdicts = [r.verdict for r in valid]
        worst = _worst_verdict(verdicts)

        return ConsensusResult(
            reviews=reviews,
            consensus_issues=consensus,
            majority_issues=majority,
            individual_issues=individual,
            worst_verdict=worst,
            participating_models=[r.model_name for r in valid],
        )
