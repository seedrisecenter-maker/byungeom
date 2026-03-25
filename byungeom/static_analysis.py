"""
Run ruff and bandit on a code string, return structured results.

Usage:
    from byungeom import run_ruff, run_bandit

    ruff_result = await run_ruff(code)
    bandit_result = await run_bandit(code)
"""

import asyncio
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Issue:
    """A single static analysis finding."""

    tool: str           # "ruff" or "bandit"
    code: str           # rule/test id, e.g. "E501", "B101"
    message: str
    line: int = 0
    severity: str = ""  # bandit: LOW / MEDIUM / HIGH; ruff: empty


@dataclass
class StaticResult:
    """Aggregated result from one static analysis tool."""

    tool: str
    issues: list[Issue] = field(default_factory=list)
    available: bool = True   # False when the tool is not installed
    raw_output: str = ""


async def _run_tool(
    cmd: list[str],
    stdin_data: bytes | None = None,
) -> tuple[int, str, str]:
    """Run an external command, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(stdin_data), timeout=30
        )
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return -1, "", "타임아웃"
    except FileNotFoundError:
        # tool not installed
        return -2, "", "tool not found"
    except Exception as exc:
        return -1, "", str(exc)


async def run_ruff(code: str) -> StaticResult:
    """
    Run ruff on the given code string.

    Requires ruff to be installed; gracefully returns an empty result if not.
    Uses JSON output format for reliable parsing.

    Args:
        code: Python source code to analyse.

    Returns:
        StaticResult with parsed issues from ruff.
    """
    clean = re.sub(r"^```\w*\n?", "", code.strip())
    clean = re.sub(r"\n?```$", "", clean.strip())

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(clean)
            tmp_path = Path(tmp.name)

        rc, stdout, stderr = await _run_tool(
            ["ruff", "check", "--output-format", "json", str(tmp_path)]
        )

        if rc == -2:
            # ruff not installed
            return StaticResult(tool="ruff", available=False, raw_output="ruff를 찾을 수 없음")

        issues: list[Issue] = []
        try:
            data = json.loads(stdout) if stdout.strip() else []
            for entry in data:
                issues.append(
                    Issue(
                        tool="ruff",
                        code=entry.get("code", ""),
                        message=entry.get("message", ""),
                        line=entry.get("location", {}).get("row", 0),
                        severity="",
                    )
                )
        except json.JSONDecodeError:
            # fall back to empty list; keep raw output for debugging
            pass

        return StaticResult(
            tool="ruff",
            issues=issues,
            available=True,
            raw_output=stdout,
        )

    except Exception as exc:
        return StaticResult(tool="ruff", available=False, raw_output=str(exc))
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


async def run_bandit(code: str) -> StaticResult:
    """
    Run bandit on the given code string.

    Requires bandit to be installed; gracefully returns an empty result if not.
    Uses JSON output format for reliable parsing.

    Args:
        code: Python source code to analyse.

    Returns:
        StaticResult with parsed security issues from bandit.
    """
    clean = re.sub(r"^```\w*\n?", "", code.strip())
    clean = re.sub(r"\n?```$", "", clean.strip())

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(clean)
            tmp_path = Path(tmp.name)

        rc, stdout, stderr = await _run_tool(
            ["bandit", "-f", "json", "-q", str(tmp_path)]
        )

        if rc == -2:
            return StaticResult(tool="bandit", available=False, raw_output="bandit를 찾을 수 없음")

        issues: list[Issue] = []
        try:
            # bandit exits with code 1 when it finds issues — that is normal
            combined = stdout if stdout.strip() else stderr
            data = json.loads(combined) if combined.strip() else {}
            for entry in data.get("results", []):
                issues.append(
                    Issue(
                        tool="bandit",
                        code=entry.get("test_id", ""),
                        message=entry.get("issue_text", ""),
                        line=entry.get("line_number", 0),
                        severity=entry.get("issue_severity", ""),
                    )
                )
        except json.JSONDecodeError:
            pass

        return StaticResult(
            tool="bandit",
            issues=issues,
            available=True,
            raw_output=stdout,
        )

    except Exception as exc:
        return StaticResult(tool="bandit", available=False, raw_output=str(exc))
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
