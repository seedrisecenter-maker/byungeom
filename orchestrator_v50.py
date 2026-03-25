#!/usr/bin/env python3
"""
변검(變臉) 에이전트 v5.0 — Star Chamber + Static Analysis + Real Execution
══════════════════════════════════════════════════════════════════════════════

v4.1 → v5.0 변경:

  1.  Star Chamber (合 다중 모델 합의 검증)
        - 합(合) 단계에 Gemini 2.5 Flash/Pro 병렬 교차 검증 추가
        - GOOGLE_API_KEY 환경변수 미설정 시 Claude 단독 모드로 폴백
        - Consensus (양쪽 동의) → 필수 수정, Individual (단독) → 검토 권고
        - 최종 판정: 가장 보수적인 판정(WORST) 채택
        - --gemini 플래그로 활성화

  2.  정적 분석 레이어 (反 코드 생성 후, 合 공격 전)
        - ruff check --select E,F,W,I,S,B --output-format json 실행
        - bandit -f json -ll 실행
        - 발견된 이슈를 合 프롬프트에 주입 (LLM이 실제 이슈를 인지)
        - ruff/bandit 미설치 시 조용히 건너뜀 (graceful skip)
        - --static-analysis 플래그로 활성화

  3.  실행 검증 (compile() 통과 후)
        - 코드를 실제 subprocess에서 실행
        - if __name__ == "__main__" 또는 def test_ 함수 감지 시 실행
        - stdout, stderr, return code 캡처
        - 타임아웃: 10초
        - 실행 결과를 合 프롬프트에 주입
        - --execute 플래그로 활성화

  4.  아키텍처 개선
        - v4.1 CLI 인터페이스 완전 호환 (기존 argparse 그대로)
        - 신규 플래그: --gemini, --static-analysis, --execute, --all-upgrades
        - --all-upgrades: 모든 v5.0 기능 일괄 활성화
        - 기존 claude -p subprocess 방식 유지

  5.  合 프롬프트 강화
        - 정적 분석 결과 포함 (활성화 시)
        - 실행 결과 포함 (활성화 시)
        - 교차 모델 공격 결과 포함 (활성화 시)
        - 이전 공격 히스토리 (v4.1 유지)

v4.1 유지:
  - Claude CLI 직접 실행 (subprocess claude -p), 과금 $0
  - asyncio subprocess 비동기 호출 (블로킹 없음)
  - 最고 코드 추적 (루프 전체에서 가장 높은 판정 코드 보존)
  - 합(合) 공격 히스토리 (반복 공격 방지)
  - 지수 백오프 (CLI 실패 시 1s→2s→4s, jitter 포함)
  - 타임아웃 복잡도 연동 (LOW 90s, MID 120s, HIGH 180s)
  - 反P 패치 모드 (경미 WOUNDED 부분 수정)
  - compile() 구문 검증 + 反P 즉시 수정
  - 합(合) 선행 루프 (合→正→反/反P)
  - 복잡도 키워드 추정 (estimate_cx)
  - goal hash 메모리 네임스페이스 (SQLite)
  - patch_streak 제한 (3연속)
  - --consecutive 모드 (최근 3중 2회 ALIVE 종료)
  - 루프 요약 (전체 판정 히스토리)
  - workspace/best.py 자동 저장

실행:
  python orchestrator_v50.py "만들 것"
  python orchestrator_v50.py "만들 것" --loops 5
  python orchestrator_v50.py "만들 것" --model opus
  python orchestrator_v50.py "만들 것" --sim-only
  python orchestrator_v50.py "만들 것" --consecutive
  python orchestrator_v50.py "JWT 인증 API" --gemini
  python orchestrator_v50.py "웹 크롤러" --static-analysis
  python orchestrator_v50.py "퀵소트" --execute
  python orchestrator_v50.py "파서" --all-upgrades

경로:
  workspace/assembly/   ← 루프별 결과
  workspace/best.py     ← 최고 판정 코드
  workspace/memory.db   ← SQLite 로컬 기억
  workspace/pipeline.md ← 실행 로그
══════════════════════════════════════════════════════════════════════════════
"""

import io
import json
import os
import re
import sys
import sqlite3
import hashlib
import argparse
import asyncio
import random
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

# Windows cp949 -> UTF-8 강제 (한자/특수문자 출력 보장)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# Gemini optional import — google-generativeai 미설치 시 조용히 건너뜀
try:
    import google.generativeai as genai  # type: ignore[import]

    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

# ── 경로 ──────────────────────────────────────────────────────────────────────
WS = Path("workspace")
ASMDIR = WS / "assembly"
DB = WS / "memory.db"
LOG = WS / "pipeline.md"
BEST = WS / "best.py"

# ── 역할 키 상수 ─────────────────────────────────────────────────────────────
JUNG = "正"
BAN = "反"
BAN_P = "反P"
HAP_L = "合L"
HAP_H = "合H"

# ── 역할별 모델 (claude CLI --model 플래그) ──────────────────────────────────
M: dict[str, str] = {
    JUNG: "haiku",
    BAN: "sonnet",
    BAN_P: "sonnet",
    HAP_L: "haiku",
    HAP_H: "sonnet",
}

# ── 복잡도별 타임아웃 (초) ───────────────────────────────────────────────────
CX_TIMEOUT: dict[str, int] = {"LOW": 90, "MID": 120, "HIGH": 180}

# ── Gemini 모델 선택 ─────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"

# ── 실행 검증 타임아웃 (초) ─────────────────────────────────────────────────
EXEC_TIMEOUT = 10

MAX_RETRIES = 2

# ── 역할별 시스템 프롬프트 ───────────────────────────────────────────────────
SYS: dict[str, str] = {
    JUNG: (
        "[역할] 변검 正(정·설계자). 계약·인터페이스만 정의. 구현 금지. 지정 태그만 출력.\n"
        "복잡도가 예상보다 높으면 [CX]MID 또는 HIGH[/CX]로 상향 가능.\n"
    ),
    BAN: (
        "[역할] 변검 反(반·구현자). 테스트 통과하는 최소 코드. 과잉 구현 금지.\n"
        "출력 전 합(合)의 공격을 시뮬하여 취약점 제거. 지정 태그만 출력.\n"
    ),
    BAN_P: (
        "[역할] 변검 反P(반·패치). 기존 코드에서 지적된 부분만 최소 수정.\n"
        "전체 재작성 금지. 수정된 전체 코드를 [CODE] 태그에 출력. 지정 태그만 출력.\n"
    ),
    HAP_L: (
        "[역할] 변검 合(합·검증자). 칭찬 금지. 공격만. "
        "ALIVE/WOUNDED/DEAD 판정. 태그만 출력.\n"
    ),
    HAP_H: (
        "[역할] 변검 合(합·심층검증). 칭찬 금지. 보안·동시성·엣지케이스 무자비 공격.\n"
        "ALIVE/WOUNDED/DEAD 판정. 태그만 출력.\n"
    ),
}

# ── 복잡도 키워드 규칙 ───────────────────────────────────────────────────────
_CX_HIGH = frozenset(
    {
        "async",
        "await",
        "thread",
        "lock",
        "mutex",
        "concurrent",
        "parallel",
        "websocket",
        "socket",
        "ssl",
        "tls",
        "encrypt",
        "decrypt",
        "jwt",
        "oauth",
        "token",
        "auth",
        "session",
        "csrf",
        "xss",
        "injection",
        "authentication",
        "authenticate",
        "authorization",
        "authorize",
        "encryption",
        "encrypted",
        "database",
        "transaction",
        "rollback",
        "migration",
        "orm",
        "queue",
        "worker",
        "celery",
        "kafka",
        "redis",
        "동시성",
        "비동기",
        "암호화",
        "인증",
        "트랜잭션",
        "보안",
    }
)
_CX_MID = frozenset(
    {
        "api",
        "rest",
        "http",
        "request",
        "response",
        "endpoint",
        "class",
        "inherit",
        "abstract",
        "interface",
        "pattern",
        "file",
        "read",
        "write",
        "parse",
        "json",
        "yaml",
        "xml",
        "csv",
        "test",
        "mock",
        "fixture",
        "validate",
        "schema",
        "cache",
        "retry",
        "timeout",
        "error",
        "exception",
        "클래스",
        "파일",
        "파싱",
        "검증",
        "캐시",
        "에러",
    }
)


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isascii() and text[start - 1].isalpha():
        return False
    if end < len(text) and text[end].isascii() and text[end].isalpha():
        return False
    return True


def estimate_cx(goal: str, contract: str = "") -> str:
    text = f"{goal} {contract}".lower()
    words = set(text.split())
    if words & _CX_HIGH:
        return "HIGH"
    if words & _CX_MID:
        return "MID"
    for kw in _CX_HIGH:
        if len(kw) >= 2:
            pos = text.find(kw)
            if pos >= 0 and _is_word_boundary(text, pos, pos + len(kw)):
                return "HIGH"
    for kw in _CX_MID:
        if len(kw) >= 2:
            pos = text.find(kw)
            if pos >= 0 and _is_word_boundary(text, pos, pos + len(kw)):
                return "MID"
    return "LOW"


# ══════════════════════════════════════════════════════════
# compile() 구문 검증
# ══════════════════════════════════════════════════════════


def syntax_check(code: str) -> str | None:
    """Return syntax error string, or None if clean."""
    clean = re.sub(r"^```\w*\n?", "", code.strip())
    clean = re.sub(r"\n?```$", "", clean.strip())
    if not clean.strip():
        return "빈 코드"
    try:
        compile(clean, "<code>", "exec")
        return None
    except SyntaxError as e:
        return f"L{e.lineno}: {e.msg}"


def _strip_fences(code: str) -> str:
    """Remove markdown code fences from a code string."""
    clean = re.sub(r"^```\w*\n?", "", code.strip())
    clean = re.sub(r"\n?```$", "", clean.strip())
    return clean


def _code_line_count(code: str) -> int:
    """Count non-empty lines in code."""
    return sum(1 for line in code.splitlines() if line.strip())


# ══════════════════════════════════════════════════════════
# v5.0 — 정적 분석 레이어
# ══════════════════════════════════════════════════════════


def _tool_available(name: str) -> bool:
    """Check if a CLI tool is on PATH without raising."""
    try:
        result = subprocess.run(
            [name, "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def run_static_analysis(code: str) -> str:
    """
    Run ruff and bandit on the given code string.
    Returns a compact summary string for injection into 合 prompt.
    Silently skips tools that are not installed.
    """
    clean = _strip_fences(code)
    if not clean.strip():
        return ""

    findings: list[str] = []

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(clean)
        tmp_path = tmp.name

    try:
        # ── ruff ─────────────────────────────────────────────────────────
        if _tool_available("ruff"):
            try:
                ruff_result = subprocess.run(
                    [
                        "ruff",
                        "check",
                        "--select",
                        "E,F,W,I,S,B",
                        "--output-format",
                        "json",
                        tmp_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if ruff_result.stdout.strip():
                    issues = json.loads(ruff_result.stdout)
                    if issues:
                        ruff_summary = "; ".join(
                            f"L{i.get('location', {}).get('row', '?')} "
                            f"{i.get('code', '')} {i.get('message', '')}"
                            for i in issues[:8]
                        )
                        findings.append(f"[ruff:{len(issues)}건] {ruff_summary}")
            except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
                pass

        # ── bandit ───────────────────────────────────────────────────────
        if _tool_available("bandit"):
            try:
                bandit_result = subprocess.run(
                    ["bandit", "-f", "json", "-ll", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                combined = bandit_result.stdout or bandit_result.stderr
                if combined.strip():
                    data = json.loads(combined)
                    results = data.get("results", [])
                    if results:
                        b_summary = "; ".join(
                            f"L{r.get('line_number', '?')} "
                            f"{r.get('test_id', '')} "
                            f"{r.get('issue_text', '')[:60]}"
                            for r in results[:5]
                        )
                        findings.append(f"[bandit:{len(results)}건] {b_summary}")
            except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
                pass
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass

    return " | ".join(findings) if findings else ""


# ══════════════════════════════════════════════════════════
# v5.0 — 실행 검증 레이어
# ══════════════════════════════════════════════════════════


def run_execution_check(code: str) -> str:
    """
    Execute the code in an isolated subprocess.
    Detects if __main__ block or test_ functions exist.
    Returns a compact result string for injection into 合 prompt.
    """
    clean = _strip_fences(code)
    if not clean.strip():
        return ""

    # Determine whether there is something to execute
    has_main = "if __name__" in clean and "__main__" in clean
    has_tests = bool(re.search(r"^\s*def\s+test_", clean, re.MULTILINE))

    if not (has_main or has_tests):
        return "[실행검증] 실행 진입점 없음 (건너뜀)"

    # Build a runner script
    runner_lines = [clean]
    if has_tests and not has_main:
        # Collect test function names and call them
        test_fns = re.findall(r"^\s*def\s+(test_\w+)", clean, re.MULTILINE)
        runner_lines.append("\n# auto-injected by 변검 v5.0 execution checker")
        runner_lines.append("import traceback as _tb")
        runner_lines.append("_passed, _failed = 0, 0")
        for fn in test_fns[:10]:
            runner_lines.append(
                f"try:\n    {fn}()\n    _passed += 1\n"
                f"except Exception as _e:\n    _failed += 1\n"
                f"    print(f'FAIL {fn}: {{_e}}')"
            )
        runner_lines.append("print(f'tests: {{_passed}} passed, {{_failed}} failed')")

    runner_code = "\n".join(runner_lines)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(runner_code)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=EXEC_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
        rc = result.returncode
        out = result.stdout.strip()[:300]
        err = result.stderr.strip()[:200]

        parts = [f"[실행검증] rc={rc}"]
        if out:
            parts.append(f"stdout={out!r}")
        if err:
            parts.append(f"stderr={err!r}")
        return " | ".join(parts)

    except subprocess.TimeoutExpired:
        return f"[실행검증] 타임아웃({EXEC_TIMEOUT}s) — 무한루프 또는 블로킹 IO 의심"
    except OSError as e:
        return f"[실행검증] 실행 불가: {e}"
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════
# v5.0 — Gemini Star Chamber 검증
# ══════════════════════════════════════════════════════════


def _gemini_hap_prompt(
    loop: int,
    goal: str,
    contract: str,
    code: str,
    hint: str,
    static_info: str,
    exec_info: str,
) -> str:
    """Build the Gemini Star Chamber attack prompt (English for cross-model clarity)."""
    parts = [
        f"You are a ruthless code verifier (Star Chamber — Gemini node). Loop {loop}.",
        f"GOAL: {goal[:80]}",
        f"CONTRACT: {contract[:120]}",
        f"CODE:\n{code[:1200]}",
    ]
    if hint:
        parts.append(f"HINT from implementer: {hint[:80]}")
    if static_info:
        parts.append(f"STATIC ANALYSIS: {static_info}")
    if exec_info:
        parts.append(f"EXECUTION RESULT: {exec_info}")
    parts.append(
        "Instructions:\n"
        "- Do NOT praise. Attack ruthlessly.\n"
        "- Check logic, edge cases, security, error handling.\n"
        "- Verdict MUST be one of: ALIVE / WOUNDED / DEAD\n"
        "Output format (tags only):\n"
        "[V]ALIVE|WOUNDED|DEAD[/V]\n"
        "[A]attack description[/A]\n"
        "[S]1-5 severity[/S]"
    )
    return "\n".join(parts)


def call_gemini_hap(
    loop: int,
    goal: str,
    contract: str,
    code: str,
    hint: str,
    static_info: str,
    exec_info: str,
) -> str:
    """
    Synchronous Gemini Star Chamber call.
    Returns raw response text, or empty string on any failure.
    """
    if not _GENAI_AVAILABLE:
        return ""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return ""

    prompt = _gemini_hap_prompt(
        loop, goal, contract, code, hint, static_info, exec_info
    )
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text or ""
    except Exception:
        return ""


# ── Star Chamber verdict merge ────────────────────────────────────────────────

_VERDICT_RANK: dict[str, int] = {"DEAD": 0, "WOUNDED": 1, "ALIVE": 2, "INIT": 1}


def _worst_verdict(a: str, b: str) -> str:
    """Return the more conservative (worse) verdict between two."""
    ra = _VERDICT_RANK.get(a, 1)
    rb = _VERDICT_RANK.get(b, 1)
    return a if ra <= rb else b


def _classify_consensus(
    claude_verdict: str,
    gemini_verdict: str,
    claude_attack: str,
    gemini_attack: str,
) -> tuple[str, str, str]:
    """
    Classify Star Chamber result.

    Returns:
        final_verdict: most conservative verdict
        consensus_type: "CONSENSUS" | "INDIVIDUAL_CLAUDE" | "INDIVIDUAL_GEMINI"
        combined_attack: merged attack summary
    """
    final = _worst_verdict(claude_verdict, gemini_verdict)

    if claude_verdict == gemini_verdict:
        consensus_type = "CONSENSUS"
        combined = f"[합의] Claude: {claude_attack[:80]} | Gemini: {gemini_attack[:80]}"
    elif claude_verdict != gemini_verdict:
        if _VERDICT_RANK.get(claude_verdict, 1) < _VERDICT_RANK.get(gemini_verdict, 1):
            consensus_type = "INDIVIDUAL_CLAUDE"
            combined = (
                f"[Claude 단독 공격({claude_verdict})] {claude_attack[:100]} "
                f"| [Gemini 의견({gemini_verdict})] {gemini_attack[:60]}"
            )
        else:
            consensus_type = "INDIVIDUAL_GEMINI"
            combined = (
                f"[Gemini 단독 공격({gemini_verdict})] {gemini_attack[:100]} "
                f"| [Claude 의견({claude_verdict})] {claude_attack[:60]}"
            )
    else:
        consensus_type = "CONSENSUS"
        combined = claude_attack[:100]

    return final, consensus_type, combined


# ══════════════════════════════════════════════════════════
# SQLite 로컬 메모리
# ══════════════════════════════════════════════════════════


class Mem:
    _STOP = frozenset(
        {
            "이",
            "가",
            "을",
            "를",
            "의",
            "에",
            "은",
            "는",
            "으로",
            "에서",
            "and",
            "the",
            "a",
            "to",
            "for",
            "is",
            "in",
            "of",
            "it",
        }
    )
    _MAX_PER_GEAR = 15
    G_JUNG = "정"
    G_BAN = "반"
    G_HAP = "합"

    def __init__(self, goal_hash: str):
        self.ns = goal_hash
        self.conn = sqlite3.connect(str(DB), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pieces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ns TEXT, gear TEXT, loop INTEGER,
                content TEXT, keywords TEXT,
                verdict TEXT DEFAULT '',
                ts TEXT
            )
        """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ns_gear ON pieces(ns, gear)"
        )
        self.conn.commit()
        self._prune_ctr = 0

    def save_batch(self, items: list[tuple]):
        ts = datetime.now().isoformat()
        with self.conn:
            self.conn.executemany(
                "INSERT INTO pieces(ns,gear,loop,content,keywords,verdict,ts) "
                "VALUES(?,?,?,?,?,?,?)",
                [
                    (self.ns, g, lp, c[:1200], self._kw(c), v, ts)
                    for g, lp, c, v in items
                ],
            )
        self._prune_ctr += 1
        if self._prune_ctr % 3 == 0:
            self._prune()

    def recall(self, query: str, gear: str, k: int = 2) -> str:
        raw = self._kw(query)
        if not raw:
            return ""
        qkw = set(raw.split(","))
        rows = self.conn.execute(
            "SELECT content, keywords, verdict FROM pieces "
            "WHERE ns=? AND gear=? ORDER BY loop DESC LIMIT 6",
            (self.ns, gear),
        ).fetchall()
        if not rows:
            return ""
        scored = sorted(
            ((len(qkw & set(kw.split(","))), c[:80], v) for c, kw, v in rows),
            key=lambda x: x[0],
            reverse=True,
        )
        return " | ".join(f"[{v}]{c}" for s, c, v in scored[:k] if s > 0)

    def _prune(self):
        for gear in (self.G_JUNG, self.G_BAN, self.G_HAP):
            self.conn.execute(
                "DELETE FROM pieces WHERE ns=? AND gear=? AND id NOT IN "
                "(SELECT id FROM pieces WHERE ns=? AND gear=? "
                "ORDER BY loop DESC LIMIT ?)",
                (self.ns, gear, self.ns, gear, self._MAX_PER_GEAR),
            )
        self.conn.commit()

    def close(self):
        self._prune()
        self.conn.close()

    @classmethod
    def _kw(cls, text: str) -> str:
        words = (w.strip(".,[](){}'\"`") for w in text.lower().split())
        seen: dict[str, None] = {}
        for w in words:
            if len(w) >= 2 and w not in cls._STOP and w not in seen:
                seen[w] = None
                if len(seen) >= 10:
                    break
        return ",".join(seen)


# ══════════════════════════════════════════════════════════
# 변검 엔진 v5.0
# ══════════════════════════════════════════════════════════


class Engine:
    def __init__(
        self,
        goal: str,
        model_override: str = "",
        use_gemini: bool = False,
        use_static: bool = False,
        use_execute: bool = False,
    ):
        WS.mkdir(exist_ok=True)
        ASMDIR.mkdir(parents=True, exist_ok=True)

        self.goal_hash = hashlib.sha256(goal.encode()).hexdigest()[:12]
        self.mem = Mem(self.goal_hash)
        self.total_calls = 0

        # v5.0 feature flags
        self.use_gemini = use_gemini and _GENAI_AVAILABLE and bool(
            os.environ.get("GOOGLE_API_KEY", "")
        )
        self.use_static = use_static
        self.use_execute = use_execute

        # Best code tracking
        self._best_code: str = ""
        self._best_verdict: str = "DEAD"
        self._best_loop: int = 0

        # Attack history (prevent repetition)
        self._attack_history: list[str] = []

        if model_override:
            for k in M:
                M[k] = model_override

    # ── asyncio subprocess CLI 호출 ─────────────────────────────────────
    async def _cli(self, gear: str, prompt: str, cx: str = "LOW") -> str:
        """Async claude -p call. Zero cost under MAX plan."""
        full_prompt = f"{SYS[gear]}\n---\n{prompt}"
        model = M[gear]
        timeout = CX_TIMEOUT.get(cx, 120)

        cmd = [
            "claude",
            "-p",
            "--model",
            model,
            "--output-format",
            "text",
            "--no-chrome",
        ]

        for attempt in range(MAX_RETRIES):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(full_prompt.encode("utf-8")),
                    timeout=timeout,
                )
                out = stdout.decode("utf-8", errors="replace").strip()

                if proc.returncode == 0 and out:
                    self.total_calls += 1
                    return out

                # Empty response — use stderr hint
                err = stderr.decode("utf-8", errors="replace").strip()
                if err:
                    self._log(f"  {gear} stderr: {err[:120]}")

                if attempt < MAX_RETRIES - 1:
                    wait = (2**attempt) + random.uniform(0, 1)
                    self._log(f"  {gear} 빈 응답 → 재시도({attempt + 1}) {wait:.1f}s")
                    await asyncio.sleep(wait)
                else:
                    return f"[{gear}오류] {MAX_RETRIES}회 빈 응답"

            except asyncio.TimeoutError:
                self._log(f"  {gear} 타임아웃({timeout}s)")
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return f"[{gear}오류] 타임아웃"
            except Exception as e:
                self._log(f"  {gear} 예외: {e}")
                return f"[{gear}오류] {e}"

        return ""

    # ── 일반 호출 (태그 검증 + 1회 재시도) ──────────────────────────────
    async def _call(
        self,
        gear: str,
        prompt: str,
        require_tags: list[str] | None = None,
        cx: str = "LOW",
    ) -> str:
        text = await self._cli(gear, prompt, cx)
        if require_tags and any(not self.x(text, t) for t in require_tags):
            self._log(f"  {gear} 태그누락 → 재시도")
            text2 = await self._cli(gear, prompt, cx)
            if all(self.x(text2, t) for t in require_tags):
                return text2
        return text

    # ── 반(反) 전체 작성 ───────────────────────────────────────────────
    async def _call_ban(self, prompt: str, cx: str = "LOW") -> str:
        text = await self._cli(BAN, prompt, cx)
        if not self.x(text, "CODE"):
            self._log("  반(反) CODE누락 → 재시도")
            text2 = await self._cli(BAN, prompt, cx)
            if self.x(text2, "CODE"):
                return text2
        return text

    # ── 반P(反P) 패치 모드 ─────────────────────────────────────────────
    async def _call_ban_patch(
        self,
        prev_code: str,
        attack: str,
        syntax_err: str = "",
        cx: str = "LOW",
    ) -> str:
        parts = ["기존코드 최소 수정만."]
        if syntax_err:
            parts.append(f"구문에러수정:{syntax_err}")
        if attack:
            parts.append(f"공격:{attack[:150]}")
        parts.append(f"코드:\n{prev_code[:1000]}")
        parts.append("[CODE]수정된 전체 코드[/CODE]")
        prompt = "\n".join(parts)

        text = await self._cli(BAN_P, prompt, cx)
        if not self.x(text, "CODE"):
            self._log("  반P(反P) CODE누락 → 재시도")
            text2 = await self._cli(BAN_P, prompt, cx)
            if self.x(text2, "CODE"):
                return text2
        return text

    # ── 합(合) 라우팅 ──────────────────────────────────────────────────
    async def _call_hap(self, prompt: str, cx: str) -> str:
        gear = HAP_H if cx == "HIGH" else HAP_L
        return await self._call(gear, prompt, require_tags=["V"], cx=cx)

    # ── 태그 추출 ──────────────────────────────────────────────────────
    @staticmethod
    def x(text: str, tag: str) -> str:
        s = text.find(f"[{tag}]")
        e = text.find(f"[/{tag}]")
        return text[s + len(tag) + 2 : e].strip() if s >= 0 and e > s else ""

    # ── 프롬프트 빌더 ──────────────────────────────────────────────────

    def _prompt_jung(
        self,
        goal: str,
        loop: int,
        fb: str,
        prev_contract: str = "",
        failed: bool = False,
    ) -> str:
        mem = self.mem.recall(goal, Mem.G_JUNG)
        parts = [f"L{loop}.목표:{goal[:70]}"]
        if fb:
            parts.append(f"합(合)FB:{fb[:60]}")
        if prev_contract and failed:
            parts.append(
                f"이전계약(실패-원인참고,다른설계):{prev_contract[:100]}"
            )
        elif prev_contract:
            parts.append(f"이전계약(보완):{prev_contract[:100]}")
        if mem:
            parts.append(f"선례:{mem}")
        parts.append(
            "계약정의(구현금지):\n"
            "[C]입출력/핵심동작[/C]\n[N]반(反)지시[/N]\n[CX]LOW|MID|HIGH[/CX]"
        )
        return "\n".join(parts)

    def _prompt_ban(
        self, goal: str, loop: int, contract: str, attack: str = ""
    ) -> str:
        mem = self.mem.recall(goal, Mem.G_BAN)
        parts = [f"L{loop}.목표:{goal[:50]}"]
        if mem:
            parts.append(f"에러:{mem}")
        if attack:
            parts.append(f"공격수정:{attack[:150]}")
        parts.append(f"계약:{contract[:120]}")
        parts.append(
            "최소구현:\n[CODE]```python\n...\n```[/CODE]\n[W]약점[/W]"
        )
        return "\n".join(parts)

    def _prompt_hap(
        self,
        loop: int,
        goal: str,
        contract: str,
        code: str,
        hint: str,
        syn_info: str = "",
        static_info: str = "",
        exec_info: str = "",
        star_chamber_info: str = "",
    ) -> str:
        """Build 合 attack prompt with all v5.0 context layers injected."""
        parts = [f"L{loop}코드공격."]
        parts.append(f"목표:{goal[:80]}")
        parts.append(f"계약:{contract[:120]}")
        parts.append(f"코드:\n{code[:1200]}")
        if hint:
            parts.append(f"힌트:{hint[:80]}")
        if syn_info:
            parts.append(f"구문검증:{syn_info}")
        # v5.0: Static analysis injection
        if static_info:
            parts.append(f"정적분석결과:{static_info[:300]}")
        # v5.0: Execution result injection
        if exec_info:
            parts.append(f"실행검증결과:{exec_info[:200]}")
        # v5.0: Star Chamber cross-model injection
        if star_chamber_info:
            parts.append(f"Gemini교차검증:{star_chamber_info[:200]}")
        # Attack history (prevent repetition)
        if self._attack_history:
            recent = self._attack_history[-3:]
            parts.append(
                f"이전공격(반복금지):{' | '.join(a[:40] for a in recent)}"
            )
        parts.append(
            "[V]ALIVE/WOUNDED/DEAD[/V]\n[A]공격[/A]\n[F]정(正)방향수정[/F]"
            "\n[S]1-5심각도[/S]"
        )
        return "\n".join(parts)

    # ── 판정 ───────────────────────────────────────────────────────────
    @staticmethod
    def verdict(text: str) -> str:
        u = text.upper().split()
        if "ALIVE" in u:
            return "ALIVE"
        if "DEAD" in u:
            return "DEAD"
        raw = text.upper()
        if "ALIVE" in raw:
            return "ALIVE"
        if raw.rstrip(".,!?;: ").endswith("DEAD"):
            return "DEAD"
        return "WOUNDED"

    @staticmethod
    def parse_sev(text: str) -> int:
        try:
            return max(1, min(5, int(text.strip()[:1])))
        except (ValueError, IndexError):
            return 3

    # ── 최고 코드 갱신 ─────────────────────────────────────────────────
    def _update_best(self, loop: int, code: str, verdict: str):
        rank = {"DEAD": 0, "INIT": 1, "WOUNDED": 2, "ALIVE": 3}
        cur = rank.get(verdict, 0)
        prev = rank.get(self._best_verdict, 0)
        if cur > prev or (cur == prev and code.strip()):
            self._best_code = code
            self._best_verdict = verdict
            self._best_loop = loop

    def _save_best(self):
        """Save highest-verdict code to best.py."""
        if not self._best_code.strip():
            return
        clean = _strip_fences(self._best_code)
        BEST.write_text(
            f"# 변검 v5.0 — Loop {self._best_loop} ({self._best_verdict})\n"
            f"# 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{clean}\n",
            encoding="utf-8",
        )

    # ── 파일 IO ────────────────────────────────────────────────────────
    def save_asm(self, loop: int, code: str, verdict: str):
        (ASMDIR / f"L{loop}.txt").write_text(
            f"{verdict}\n{code}", encoding="utf-8"
        )

    def _log(self, msg: str):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")

    # ══════════════════════════════════════════════════════
    # v5.0 — Star Chamber 합(合) 통합 호출
    # ══════════════════════════════════════════════════════

    async def _star_chamber_hap(
        self,
        loop: int,
        goal: str,
        contract: str,
        code: str,
        hint: str,
        prev_cx: str,
        static_info: str,
        exec_info: str,
    ) -> tuple[str, str, str, int, str]:
        """
        Run Star Chamber verification: Claude + optional Gemini in parallel.

        Returns:
            (verdict, attack, fb, severity, consensus_label)
        """
        # Build Claude 합 prompt (with all context)
        syn = syntax_check(code)
        syn_info = f"구문에러:{syn}" if syn else "구문OK"

        # Run Gemini in a thread if enabled (sync API)
        gemini_raw = ""
        gemini_future: asyncio.Future | None = None

        if self.use_gemini:
            loop_obj = asyncio.get_event_loop()
            gemini_future = loop_obj.run_in_executor(
                None,
                call_gemini_hap,
                loop,
                goal,
                contract,
                code,
                hint,
                static_info,
                exec_info,
            )

        # Claude 합 call (async)
        claude_prompt = self._prompt_hap(
            loop,
            goal,
            contract,
            code,
            hint,
            syn_info,
            static_info,
            exec_info,
            star_chamber_info="",  # No recursive injection
        )
        raw_claude = await self._call_hap(claude_prompt, prev_cx)

        # Gather Gemini result
        if gemini_future is not None:
            try:
                gemini_raw = await asyncio.wait_for(gemini_future, timeout=60)
            except (asyncio.TimeoutError, Exception):
                gemini_raw = ""

        # Parse Claude result
        claude_v_raw = self.x(raw_claude, "V")
        claude_attack = self.x(raw_claude, "A")
        claude_fb = self.x(raw_claude, "F")
        claude_sev = self.parse_sev(self.x(raw_claude, "S"))
        claude_verdict = self.verdict(claude_v_raw)

        if not self.use_gemini or not gemini_raw:
            # Claude-only path
            consensus_label = "CLAUDE-ONLY"
            final_verdict = claude_verdict
            final_attack = claude_attack
            final_fb = claude_fb
            final_sev = claude_sev
        else:
            # Star Chamber merge
            gemini_v_raw = self.x(gemini_raw, "V")
            gemini_attack = self.x(gemini_raw, "A")
            gemini_sev = self.parse_sev(self.x(gemini_raw, "S"))
            gemini_verdict = self.verdict(gemini_v_raw)

            final_verdict, consensus_label, combined_attack = _classify_consensus(
                claude_verdict, gemini_verdict, claude_attack, gemini_attack
            )
            final_attack = combined_attack
            final_fb = claude_fb  # Use Claude's directional feedback
            final_sev = max(claude_sev, gemini_sev)  # Worst severity

            self._log(
                f"  Star Chamber: Claude={claude_verdict} "
                f"Gemini={gemini_verdict} → {consensus_label}/{final_verdict}"
            )

        # Syntax check override
        if syn and final_verdict == "ALIVE":
            final_verdict = "WOUNDED"
            final_sev = max(final_sev, 3)

        return final_verdict, final_attack, final_fb, final_sev, consensus_label

    # ══════════════════════════════════════════════════════
    # 하이브리드 루프 (合→正→反/反P)
    # ══════════════════════════════════════════════════════

    async def loop(self, goal: str, idx: int, state: dict) -> dict:
        calls = 0
        contract = state["contract"]
        prev_code = state["code"]
        prev_cx = state["cx"]

        attack = state.get("attack", "")
        fb = state.get("fb", "")
        sev = state.get("sev", 3)
        verd = state.get("verdict", "WOUNDED") if idx > 1 else "WOUNDED"
        consensus_label = state.get("consensus_label", "")

        # ── 1단계: 합(合) — 이전 코드 공격 (Loop 2+) ──
        if idx > 1 and prev_code:
            # v5.0: Static analysis before 合
            static_info = ""
            if self.use_static:
                static_info = run_static_analysis(prev_code)
                if static_info:
                    self._log(f"  정적분석: {static_info[:80]}")

            # v5.0: Execution check before 合
            exec_info = ""
            if self.use_execute:
                exec_info = run_execution_check(prev_code)
                if exec_info:
                    self._log(f"  실행검증: {exec_info[:80]}")

            # v5.0: Star Chamber 합(合) call
            verd, attack, fb, sev, consensus_label = await self._star_chamber_hap(
                idx,
                goal,
                contract,
                prev_code,
                state.get("hint", ""),
                prev_cx,
                static_info,
                exec_info,
            )
            calls += 1

            # Attack history (prevent repetition)
            if attack:
                self._attack_history.append(attack[:60])

            self._log(
                f"  L{idx} 합(合)->{verd}(S{sev}) [{consensus_label}]"
            )

            if verd == "ALIVE":
                self.mem.save_batch([(Mem.G_HAP, idx, attack, verd)])
                self.save_asm(idx, prev_code[:2000], verd)
                self._update_best(idx, prev_code, verd)
                return {
                    **state,
                    "verdict": verd,
                    "sev": sev,
                    "fb": fb,
                    "attack": attack,
                    "calls": calls,
                    "mode": "DONE",
                    "need_jung": False,
                    "consensus_label": consensus_label,
                }

        # ── 2단계: 正(정) — 계약 재설계 (조건부) ──
        need_jung = (
            (idx == 1) or (verd == "DEAD") or (verd == "WOUNDED" and sev >= 4)
        )
        cx = prev_cx

        if need_jung:
            failed = verd == "DEAD"
            pc = contract if idx > 1 else ""
            raw_jung = await self._call(
                JUNG,
                self._prompt_jung(goal, idx, fb, pc, failed),
                require_tags=["C"],
                cx=cx,
            )
            calls += 1
            contract = self.x(raw_jung, "C") or goal[:100]

            cx_floor = estimate_cx(goal, contract)
            cx_raw = self.x(raw_jung, "CX").upper()
            cx_jung = (
                "HIGH"
                if "HIGH" in cx_raw
                else "MID"
                if "MID" in cx_raw
                else "LOW"
            ) if cx_raw else "LOW"
            rank = {"LOW": 0, "MID": 1, "HIGH": 2}
            cx = (
                cx_jung
                if rank.get(cx_jung, 0) >= rank.get(cx_floor, 0)
                else cx_floor
            )

        # ── 3단계: 反(반) 또는 反P — 코드 작성/패치 ──
        mode = "반(反)"
        patch_streak = state.get("patch_streak", 0)

        if (
            idx > 1
            and verd == "WOUNDED"
            and sev < 4
            and prev_code
            and patch_streak < 3
        ):
            prev_lines = _code_line_count(prev_code)
            raw_ban = await self._call_ban_patch(prev_code, attack, cx=cx)
            calls += 1
            mode = "반P"
            patch_streak += 1

            # Diff detection — warn if patch is too large
            new_code = self.x(raw_ban, "CODE")
            new_lines = _code_line_count(new_code)
            diff_pct = abs(new_lines - prev_lines) / max(prev_lines, 1) * 100
            if diff_pct > 50:
                self._log(f"  반P 변경량 {diff_pct:.0f}% (과도 패치 주의)")
        else:
            inject = attack if verd in ("WOUNDED", "DEAD") else ""
            raw_ban = await self._call_ban(
                self._prompt_ban(goal, idx, contract, inject), cx
            )
            calls += 1
            patch_streak = 0

        code = self.x(raw_ban, "CODE")
        hint = (
            self.x(raw_ban, "W")
            if mode == "반(反)"
            else state.get("hint", "")
        )

        # Empty code → force DEAD
        if not code.strip():
            self._log(f"  L{idx} 빈 코드 → 강제 DEAD")
            self.mem.save_batch(
                [
                    (Mem.G_JUNG, idx, contract, ""),
                    (Mem.G_BAN, idx, "(빈 코드)", "DEAD"),
                    (Mem.G_HAP, idx, fb, "DEAD"),
                ]
            )
            self.save_asm(idx, "", "DEAD")
            return {
                "verdict": "DEAD",
                "contract": contract,
                "code": "",
                "fb": fb,
                "cx": cx,
                "hint": "",
                "attack": "빈 코드 생성됨",
                "sev": 5,
                "patch_streak": 0,
                "calls": calls,
                "mode": mode,
                "need_jung": need_jung,
                "consensus_label": consensus_label,
            }

        # ── 4단계: compile() → 에러 시 反P 즉시 수정 ──
        syn_err = syntax_check(code)
        if syn_err:
            self._log(f"  구문에러: {syn_err} → 반P 수정")
            raw_fix = await self._call_ban_patch(code, "", syn_err, cx)
            calls += 1
            fixed = self.x(raw_fix, "CODE")
            if fixed and not syntax_check(fixed):
                code = fixed
                self._log("  구문수정 성공")
            else:
                self._log("  구문수정 실패 — 원본 유지")

        # ── 5단계: 저장 + 최고 코드 갱신 ──
        self.mem.save_batch(
            [
                (Mem.G_JUNG, idx, contract, ""),
                (Mem.G_BAN, idx, raw_ban, verd),
                (Mem.G_HAP, idx, fb, verd),
            ]
        )
        v_label = verd if idx > 1 else "INIT"
        self.save_asm(idx, code[:2000], v_label)
        self._update_best(idx, code, v_label)

        self._log(
            f"  L{idx} {mode} 正{'↺' if not need_jung else '+'} "
            f"호출{calls} CX={cx} S{sev}"
        )

        return {
            "verdict": v_label,
            "contract": contract,
            "code": code,
            "fb": fb,
            "cx": cx,
            "hint": hint,
            "attack": attack,
            "sev": sev,
            "patch_streak": patch_streak,
            "calls": calls,
            "mode": mode,
            "need_jung": need_jung,
            "consensus_label": consensus_label,
        }

    # ══════════════════════════════════════════════════════
    # 메인 실행
    # ══════════════════════════════════════════════════════

    async def run(
        self,
        goal: str,
        max_loops: int,
        alive_target: int,
        sim_only: bool,
        consecutive: bool,
    ):
        # Feature flag summary for display
        v5_flags: list[str] = []
        if self.use_gemini:
            v5_flags.append("Star Chamber(Gemini)")
        if self.use_static:
            v5_flags.append("정적분석(ruff+bandit)")
        if self.use_execute:
            v5_flags.append("실행검증")
        v5_label = " + ".join(v5_flags) if v5_flags else "v4.1 호환 모드"

        print(f"\n{'=' * 62}")
        print(f"  변검 v5.0 — Claude CLI + {v5_label}")
        print(f"{'=' * 62}")
        print(f"  목표       : {goal[:50]}")
        print(
            f"  모델       : 正={M[JUNG]} 反={M[BAN]} "
            f"合L={M[HAP_L]} 合H={M[HAP_H]}"
        )
        if self.use_gemini:
            print(f"  Gemini     : {GEMINI_MODEL} (Star Chamber 활성)")
        print(f"  파이프라인  : 合→正→反/反P (비동기 subprocess)")
        print(
            f"  종료       : "
            f"{'최근3중2회' if consecutive else '누적'} ALIVE {alive_target}회"
        )
        print(f"  과금       : $0 (MAX 요금제)")
        print(f"{'─' * 62}")

        init_cx = estimate_cx(goal)
        self._log(f"  초기 CX={init_cx} v5_flags={v5_label}")

        state: dict = {
            "verdict": "DEAD",
            "contract": "",
            "code": "",
            "fb": "",
            "cx": init_cx,
            "hint": "",
            "attack": "",
            "sev": 5,
            "patch_streak": 0,
            "consensus_label": "",
        }
        t_calls = 0
        recent_v: list[str] = []
        all_v: list[str] = []
        alive_cnt = 0

        for i in range(1, max_loops + 1):
            t0 = time.time()
            print(f"\n  Loop {i}", end="", flush=True)
            try:
                r = await self.loop(goal, i, state)

                state = {
                    k: r[k]
                    for k in (
                        "verdict",
                        "contract",
                        "code",
                        "fb",
                        "cx",
                        "hint",
                        "attack",
                        "sev",
                        "patch_streak",
                        "consensus_label",
                    )
                }
                t_calls += r["calls"]
                elapsed = time.time() - t0

                recent_v.append(r["verdict"])
                all_v.append(r["verdict"])
                if len(recent_v) > 3:
                    recent_v.pop(0)

                icon = {
                    "ALIVE": "O",
                    "WOUNDED": "~",
                    "DEAD": "X",
                    "INIT": ".",
                }.get(r["verdict"], "?")
                jung_mark = "re" if not r.get("need_jung") else "+"
                sc_label = (
                    f" [{r.get('consensus_label', '')}]"
                    if r.get("consensus_label")
                    else ""
                )
                print(
                    f"  [{icon}] {r['verdict']}(S{r['sev']}) "
                    f"| {r['mode']} 正{jung_mark} "
                    f"CX={r['cx']} | 호출{t_calls} "
                    f"| {elapsed:.1f}s{sc_label}"
                )

                if sim_only and i >= 3:
                    print("  [시뮬 완료]")
                    break

                if r["verdict"] == "ALIVE":
                    alive_cnt += 1
                    if consecutive:
                        alive_recent = sum(
                            1 for v in recent_v if v == "ALIVE"
                        )
                        if alive_recent >= 2:
                            print(
                                f"\n  최근 ALIVE {alive_recent}/3 — {i}루프 완료"
                            )
                            break
                    elif alive_cnt >= alive_target:
                        print(f"\n  ALIVE {alive_target}회 달성 — {i}루프 완료")
                        break

            except KeyboardInterrupt:
                print("\n  [중단]")
                break

        # Save best code
        self._save_best()
        self.mem.close()

        # Loop summary
        print(f"\n{'=' * 62}")
        print(f"  판정 히스토리: {' → '.join(all_v)}")
        print(f"  최고 코드    : Loop {self._best_loop} ({self._best_verdict})")
        print(f"  CLI 호출     : {self.total_calls}회")
        print(f"  과금         : $0 (MAX 요금제)")
        print(f"  결과         : {WS.resolve()}")
        if self._best_code:
            print(f"  최고 코드    : {BEST.resolve()}")
        print(f"{'=' * 62}\n")


# ══════════════════════════════════════════════════════════
# 엔트리포인트
# ══════════════════════════════════════════════════════════


def main():
    p = argparse.ArgumentParser(
        description="변검 v5.0 — Claude CLI + Star Chamber + 정적분석 + 실행검증",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python orchestrator_v50.py \"피보나치 함수\"\n"
            "  python orchestrator_v50.py \"JWT 인증 API\" --loops 5\n"
            "  python orchestrator_v50.py \"퀵소트\" --model opus\n"
            "  python orchestrator_v50.py \"파서\" --sim-only\n"
            "  python orchestrator_v50.py \"서버\" --consecutive\n"
            "  python orchestrator_v50.py \"웹 API\" --gemini\n"
            "  python orchestrator_v50.py \"파서\" --static-analysis\n"
            "  python orchestrator_v50.py \"퀵소트\" --execute\n"
            "  python orchestrator_v50.py \"복잡한 시스템\" --all-upgrades\n"
            "\n"
            "과금: $0 (MAX 요금제 내에서 처리)\n"
            "API 키: claude 불필요, Gemini는 GOOGLE_API_KEY 환경변수 필요"
        ),
    )
    p.add_argument("goal", help="만들 것 (자연어)")
    p.add_argument("--loops", type=int, default=10, help="최대 루프 (기본 10)")
    p.add_argument("--alive", type=int, default=3, help="ALIVE 목표 (기본 3)")
    p.add_argument("--sim-only", action="store_true", help="3루프 시뮬")
    p.add_argument(
        "--model", default="", help="모델 오버라이드 (haiku/sonnet/opus)"
    )
    p.add_argument(
        "--consecutive", action="store_true", help="최근3중2회 ALIVE로 종료"
    )
    # v5.0 new flags
    p.add_argument(
        "--gemini",
        action="store_true",
        help="Gemini Star Chamber 교차검증 활성화 (GOOGLE_API_KEY 필요)",
    )
    p.add_argument(
        "--static-analysis",
        action="store_true",
        help="ruff + bandit 정적분석 레이어 활성화",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="코드 실제 실행 검증 활성화 (10초 타임아웃)",
    )
    p.add_argument(
        "--all-upgrades",
        action="store_true",
        help="모든 v5.0 기능 일괄 활성화 (--gemini --static-analysis --execute)",
    )

    args = p.parse_args()

    # --all-upgrades: enable all v5.0 features
    use_gemini = args.gemini or args.all_upgrades
    use_static = args.static_analysis or args.all_upgrades
    use_execute = args.execute or args.all_upgrades

    # Warn if Gemini requested but not available
    if use_gemini and not _GENAI_AVAILABLE:
        print(
            "  [경고] google-generativeai 패키지 미설치. "
            "pip install google-generativeai 후 재실행하세요. "
            "Claude 단독 모드로 진행합니다."
        )
    if use_gemini and _GENAI_AVAILABLE and not os.environ.get("GOOGLE_API_KEY"):
        print(
            "  [경고] GOOGLE_API_KEY 환경변수 미설정. "
            "Claude 단독 모드로 진행합니다."
        )

    asyncio.run(
        Engine(
            args.goal,
            args.model,
            use_gemini=use_gemini,
            use_static=use_static,
            use_execute=use_execute,
        ).run(
            goal=args.goal,
            max_loops=args.loops,
            alive_target=args.alive,
            sim_only=args.sim_only,
            consecutive=args.consecutive,
        )
    )


if __name__ == "__main__":
    main()
