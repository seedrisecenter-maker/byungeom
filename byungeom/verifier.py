"""
Core 正反合 verification logic extracted from the 변검 orchestrator.

Usage:
    from byungeom import Verifier

    v = Verifier()
    result = await v.verify(
        code="def add(a, b): return a + b",
        goal="두 수를 더하는 함수",
        run_static=True,
        run_exec=True,
    )
    print(result.verdict)       # ALIVE / WOUNDED / DEAD
    print(result.attacks)       # list of attack strings
    print(result.static_issues) # ruff/bandit findings
    print(result.exec_result)   # execution output
"""

import asyncio
import random
import re
from dataclasses import dataclass, field

from .executor import ExecResult, safe_execute
from .static_analysis import Issue, StaticResult, run_bandit, run_ruff

# ── Claude CLI constants (mirrors v4.1 orchestrator) ─────────────────────────

_DEFAULT_MODEL = "sonnet"
_HAP_SYS = (
    "[역할] 변검 合(합·검증자). 칭찬 금지. 공격만. "
    "ALIVE/WOUNDED/DEAD 판정. 태그만 출력.\n"
)
_HAP_H_SYS = (
    "[역할] 변검 合(합·심층검증). 칭찬 금지. 보안·동시성·엣지케이스 무자비 공격.\n"
    "ALIVE/WOUNDED/DEAD 판정. 태그만 출력.\n"
)
_MAX_RETRIES = 2
_DEFAULT_TIMEOUT = 120


# ── Return types ──────────────────────────────────────────────────────────────


@dataclass
class VerifyResult:
    """Structured result of a single 正反合 verification cycle."""

    verdict: str                              # ALIVE / WOUNDED / DEAD
    attacks: list[str] = field(default_factory=list)
    feedback: str = ""                        # 正 direction hint from 合
    severity: int = 3                         # 1–5
    static_issues: list[Issue] = field(default_factory=list)
    exec_result: ExecResult | None = None
    raw_hap_response: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_tag(text: str, tag: str) -> str:
    """Extract content between [TAG] … [/TAG] markers."""
    s = text.find(f"[{tag}]")
    e = text.find(f"[/{tag}]")
    return text[s + len(tag) + 2 : e].strip() if s >= 0 and e > s else ""


def _parse_verdict(text: str) -> str:
    """Return ALIVE / WOUNDED / DEAD from free-form 합(合) response."""
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


def _syntax_check(code: str) -> str | None:
    """Return error string if code has a syntax error, else None."""
    clean = re.sub(r"^```\w*\n?", "", code.strip())
    clean = re.sub(r"\n?```$", "", clean.strip())
    if not clean.strip():
        return "빈 코드"
    try:
        compile(clean, "<code>", "exec")
        return None
    except SyntaxError as e:
        return f"L{e.lineno}: {e.msg}"


# ── Verifier ──────────────────────────────────────────────────────────────────


class Verifier:
    """
    Standalone 正反合 verifier.

    Calls the Claude CLI (``claude -p``) to run the 合 (critic) role once
    against the provided code and goal, then optionally runs static analysis
    and safe code execution.

    The class is stateless between ``verify()`` calls — safe to reuse.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        timeout: float = _DEFAULT_TIMEOUT,
        deep: bool = False,
    ) -> None:
        """
        Args:
            model:   Claude model slug passed to ``--model`` (haiku/sonnet/opus).
            timeout: Seconds to wait for the Claude CLI call.
            deep:    Use the high-complexity 合H system prompt (more aggressive).
        """
        self.model = model
        self.timeout = timeout
        self._sys = _HAP_H_SYS if deep else _HAP_SYS

    # ── public API ────────────────────────────────────────────────────────────

    async def verify(
        self,
        code: str,
        goal: str,
        *,
        run_static: bool = False,
        run_exec: bool = False,
    ) -> VerifyResult:
        """
        Run one 正反合 verification cycle.

        Args:
            code:        Python source code to verify.
            goal:        Natural-language description of what the code should do.
            run_static:  Also run ruff + bandit and include findings.
            run_exec:    Also execute the code in a subprocess.

        Returns:
            VerifyResult with verdict, attacks, static issues, and exec output.
        """
        # Run 합(合) critique and optional tools concurrently
        tasks: list = [self._call_hap(code, goal)]
        if run_static:
            tasks.append(_run_static(code))
        if run_exec:
            tasks.append(safe_execute(code))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        raw_hap: str = results[0] if isinstance(results[0], str) else ""
        static_issues: list[Issue] = []
        exec_result: ExecResult | None = None

        idx = 1
        if run_static:
            sr = results[idx]
            if isinstance(sr, list):
                static_issues = sr
            idx += 1
        if run_exec:
            er = results[idx]
            if isinstance(er, ExecResult):
                exec_result = er

        # Parse 합(合) response
        v_raw = _extract_tag(raw_hap, "V")
        attack_raw = _extract_tag(raw_hap, "A")
        feedback = _extract_tag(raw_hap, "F")
        severity = _parse_severity(_extract_tag(raw_hap, "S"))
        verdict = _parse_verdict(v_raw or raw_hap)

        # Downgrade verdict if syntax is broken
        syn_err = _syntax_check(code)
        if syn_err and verdict == "ALIVE":
            verdict = "WOUNDED"
            severity = max(severity, 3)

        attacks = [a.strip() for a in attack_raw.splitlines() if a.strip()]
        if not attacks and attack_raw.strip():
            attacks = [attack_raw.strip()]

        return VerifyResult(
            verdict=verdict,
            attacks=attacks,
            feedback=feedback,
            severity=severity,
            static_issues=static_issues,
            exec_result=exec_result,
            raw_hap_response=raw_hap,
        )

    # ── internal ──────────────────────────────────────────────────────────────

    async def _call_hap(self, code: str, goal: str) -> str:
        """Build 합(合) prompt and call Claude CLI."""
        prompt = self._build_hap_prompt(code, goal)
        full = f"{self._sys}\n---\n{prompt}"
        return await self._cli(full)

    def _build_hap_prompt(self, code: str, goal: str) -> str:
        parts = [
            f"목표:{goal[:80]}",
            f"코드:\n{code[:1500]}",
        ]
        syn = _syntax_check(code)
        parts.append(f"구문검증:{'구문에러:' + syn if syn else '구문OK'}")
        parts.append(
            "[V]ALIVE/WOUNDED/DEAD[/V]\n[A]공격[/A]\n[F]정(正)방향수정[/F]\n[S]1-5심각도[/S]"
        )
        return "\n".join(parts)

    async def _cli(self, full_prompt: str) -> str:
        """
        Call ``claude -p`` as an async subprocess.
        Retries up to _MAX_RETRIES times with exponential back-off on empty output.
        """
        cmd = [
            "claude", "-p",
            "--model", self.model,
            "--output-format", "text",
            "--no-chrome",
        ]

        for attempt in range(_MAX_RETRIES):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(full_prompt.encode("utf-8")),
                    timeout=self.timeout,
                )
                out = stdout_b.decode("utf-8", errors="replace").strip()
                if proc.returncode == 0 and out:
                    return out

                # Empty output — retry with back-off
                if attempt < _MAX_RETRIES - 1:
                    wait = (2**attempt) + random.uniform(0, 0.5)
                    await asyncio.sleep(wait)
                else:
                    err = stderr_b.decode("utf-8", errors="replace").strip()
                    return f"[합오류] 빈 응답 ({err[:120]})"

            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return f"[합오류] 타임아웃({self.timeout}s)"
            except Exception as exc:
                return f"[합오류] {exc}"

        return "[합오류] 재시도 초과"


# ── Module-level helper ───────────────────────────────────────────────────────


async def _run_static(code: str) -> list[Issue]:
    """Run ruff and bandit concurrently, return merged issue list."""
    ruff_r, bandit_r = await asyncio.gather(run_ruff(code), run_bandit(code))
    issues: list[Issue] = []
    if isinstance(ruff_r, StaticResult):
        issues.extend(ruff_r.issues)
    if isinstance(bandit_r, StaticResult):
        issues.extend(bandit_r.issues)
    return issues
