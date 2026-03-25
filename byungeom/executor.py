"""
Safely execute Python code in a subprocess with timeout.

Usage:
    from byungeom import safe_execute

    result = await safe_execute(code, timeout=10)
    print(result.stdout, result.stderr, result.returncode, result.success)
"""

import asyncio
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecResult:
    """Result of a safe code execution."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    success: bool = False
    timed_out: bool = False


async def safe_execute(code: str, timeout: float = 10.0) -> ExecResult:
    """
    Execute Python code in a subprocess with a timeout.

    Writes code to a temp file, runs it with the current Python interpreter,
    captures stdout/stderr, and always cleans up the temp file.

    Args:
        code: Python source code to execute.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        ExecResult with stdout, stderr, returncode, success, timed_out fields.
    """
    # Strip fenced code blocks if present (e.g. ```python ... ```)
    import re

    clean = re.sub(r"^```\w*\n?", "", code.strip())
    clean = re.sub(r"\n?```$", "", clean.strip())

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(clean)
            tmp_path = Path(tmp.name)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return ExecResult(
                stdout="",
                stderr=f"실행 타임아웃 ({timeout}초 초과)",
                returncode=-1,
                success=False,
                timed_out=True,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1

        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            returncode=rc,
            success=(rc == 0),
            timed_out=False,
        )

    except Exception as exc:
        return ExecResult(
            stdout="",
            stderr=f"실행 오류: {exc}",
            returncode=-1,
            success=False,
            timed_out=False,
        )
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
