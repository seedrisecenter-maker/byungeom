#!/usr/bin/env python3
"""
경비(警備) 에이전트 — 프로젝트 보안 상시 감시

변검의 合(합) 공격 능력 + 정적분석을 보안 감시에 특화.
지정된 디렉토리의 Python 파일을 감시하고,
변경 발생 시 자동으로 보안 스캔을 실행한다.

기능:
  1. 파일 변경 감지 (watchdog 또는 polling)
  2. ruff 보안 룰셋 (S: bandit) 자동 실행
  3. 위험 패턴 탐지 (eval, exec, pickle, subprocess, SQL injection 등)
  4. API 키 노출 감지 (하드코딩된 시크릿)
  5. Star Chamber 다중모델 보안 리뷰 (선택)
  6. 위협 발견 시 경고 출력 + 로그 기록

실행:
  python guard_agent.py                          # 현재 디렉토리 감시
  python guard_agent.py --watch ../up            # 거래봇 감시
  python guard_agent.py --scan strategy.py       # 단일 파일 스캔
  python guard_agent.py --scan-all ../up         # 디렉토리 전체 스캔
  python guard_agent.py --star-chamber file.py   # 다중모델 보안 리뷰
"""

import argparse
import asyncio
import io
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Dangerous patterns ──────────────────────────────────────────────────────

DANGER_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, severity, description)
    (r"\beval\s*\(", "HIGH", "eval() 사용 — 코드 인젝션 위험"),
    (r"\bexec\s*\(", "HIGH", "exec() 사용 — 코드 인젝션 위험"),
    (r"\b__import__\s*\(", "HIGH", "__import__() 동적 임포트 — 코드 인젝션 위험"),
    (r"pickle\.loads?\s*\(", "HIGH", "pickle 역직렬화 — 원격 코드 실행 위험"),
    (r"subprocess\.(?:call|run|Popen)\s*\(.*shell\s*=\s*True", "HIGH", "shell=True — 커맨드 인젝션 위험"),
    (r"os\.system\s*\(", "MEDIUM", "os.system() — subprocess 권장"),
    (r"f['\"].*\{.*\}.*(?:SELECT|INSERT|UPDATE|DELETE|DROP)", "HIGH", "f-string SQL — SQL 인젝션 위험"),
    (r"\.format\s*\(.*(?:SELECT|INSERT|UPDATE|DELETE|DROP)", "HIGH", ".format() SQL — SQL 인젝션 위험"),
    (r"(?:password|secret|api_key|token)\s*=\s*['\"][^'\"]{8,}", "CRITICAL", "하드코딩된 시크릿 감지"),
    (r"AIza[A-Za-z0-9_-]{35}", "CRITICAL", "Google API 키 하드코딩 감지"),
    (r"sk-[A-Za-z0-9]{20,}", "CRITICAL", "OpenAI API 키 하드코딩 감지"),
    (r"xai-[A-Za-z0-9]{20,}", "CRITICAL", "xAI API 키 하드코딩 감지"),
    (r"ghp_[A-Za-z0-9]{36,}", "CRITICAL", "GitHub 토큰 하드코딩 감지"),
    (r"AKIA[A-Z0-9]{16}", "CRITICAL", "AWS Access Key 하드코딩 감지"),
    (r"\btelnet\b", "MEDIUM", "telnet 사용 — 암호화 없는 통신"),
    (r"verify\s*=\s*False", "MEDIUM", "SSL 검증 비활성화"),
    (r"chmod\s*\(\s*0o?777", "MEDIUM", "777 권한 — 과도한 파일 접근 허용"),
    (r"yaml\.load\s*\((?!.*Loader)", "MEDIUM", "yaml.load() Loader 미지정 — 코드 실행 위험"),
    (r"tempfile\.mk(?:s?temp)\s*\(", "LOW", "안전하지 않은 임시 파일 생성"),
]

COMPILED_PATTERNS = [
    (re.compile(p, re.IGNORECASE), sev, desc)
    for p, sev, desc in DANGER_PATTERNS
]

SEV_ICON = {"CRITICAL": "☠", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}
SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# ── Scanner ──────────────────────────────────────────────────────────────────


def scan_file(filepath: Path) -> list[dict]:
    """Scan a single file for dangerous patterns."""
    try:
        code = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    findings: list[dict] = []
    for lineno, line in enumerate(code.splitlines(), 1):
        for pattern, sev, desc in COMPILED_PATTERNS:
            if pattern.search(line):
                findings.append({
                    "file": str(filepath),
                    "line": lineno,
                    "severity": sev,
                    "description": desc,
                    "code": line.strip()[:120],
                })
    return findings


def scan_directory(dirpath: Path) -> list[dict]:
    """Scan all Python files in a directory."""
    all_findings: list[dict] = []
    for pyfile in sorted(dirpath.rglob("*.py")):
        if "__pycache__" in str(pyfile) or ".ruff_cache" in str(pyfile):
            continue
        all_findings.extend(scan_file(pyfile))
    return all_findings


async def run_ruff_security(filepath: Path) -> list[dict]:
    """Run ruff with security rules (S = bandit equivalent)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff", "check", "--select", "S", "--output-format", "json",
            str(filepath),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        import json
        data = json.loads(stdout_b.decode("utf-8", errors="replace"))
        return [
            {
                "file": str(filepath),
                "line": entry.get("location", {}).get("row", 0),
                "severity": "MEDIUM",
                "description": f"[ruff {entry.get('code', '')}] {entry.get('message', '')}",
                "code": "",
            }
            for entry in data
        ]
    except FileNotFoundError:
        return []
    except Exception:
        return []


# ── Reporter ─────────────────────────────────────────────────────────────────


def report(findings: list[dict], title: str = "보안 스캔") -> int:
    """Print formatted report. Returns count of HIGH+ issues."""
    if not findings:
        print(f"\n  ✅ {title} — 위협 없음")
        return 0

    sorted_f = sorted(findings, key=lambda f: SEV_RANK.get(f["severity"], 99))

    print(f"\n{'='*60}")
    print(f"  ⚠ {title} — {len(findings)}건 발견")
    print(f"{'='*60}")

    for f in sorted_f:
        icon = SEV_ICON.get(f["severity"], "?")
        rel_path = Path(f["file"]).name
        print(f"  {icon} [{f['severity']}] {rel_path}:L{f['line']}")
        print(f"    {f['description']}")
        if f["code"]:
            print(f"    >>> {f['code'][:100]}")
        print()

    critical = sum(1 for f in findings if f["severity"] in ("CRITICAL", "HIGH"))
    print(f"{'─'*60}")
    print(f"  CRITICAL/HIGH: {critical}건 | MEDIUM: {sum(1 for f in findings if f['severity']=='MEDIUM')}건 | LOW: {sum(1 for f in findings if f['severity']=='LOW')}건")
    print(f"{'='*60}")

    return critical


# ── Star Chamber security review ─────────────────────────────────────────────


async def star_chamber_security_review(filepath: Path) -> None:
    """Run multi-model security review using Star Chamber."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from byungeom import StarChamber
    except ImportError:
        print("  [오류] byungeom 패키지를 찾을 수 없음")
        return

    code = filepath.read_text(encoding="utf-8", errors="replace")
    goal = f"보안 취약점 탐지: {filepath.name} — SQL 인젝션, 코드 인젝션, 시크릿 노출, 권한 상승 공격"

    print(f"\n  Star Chamber 보안 리뷰: {filepath.name}")
    print(f"  다중 모델 병렬 공격 중...", end=" ", flush=True)

    sc = StarChamber(claude_model="sonnet", include_gemini=True)
    result = await sc.review(code, goal)

    print(f"완료 ({', '.join(result.participating_models)})")

    if result.consensus_issues:
        print(f"\n  🔴 합의된 위협 (전 모델 동의) — 반드시 수정:")
        for issue in result.consensus_issues:
            print(f"    - {issue[:100]}")

    if result.majority_issues:
        print(f"\n  🟡 다수 의견:")
        for issue in result.majority_issues:
            print(f"    - {issue[:100]}")

    if result.individual_issues:
        print(f"\n  🔵 개별 의견:")
        for issue in result.individual_issues[:5]:
            print(f"    - {issue[:100]}")

    print(f"\n  최종 판정: {result.worst_verdict}")


# ── Watch mode (polling) ─────────────────────────────────────────────────────


def watch_directory(dirpath: Path, interval: float = 5.0) -> None:
    """Watch directory for changes and auto-scan."""
    print(f"\n{'='*60}")
    print(f"  경비 에이전트 — 상시 감시 모드")
    print(f"  감시 대상: {dirpath.resolve()}")
    print(f"  스캔 간격: {interval}초")
    print(f"  Ctrl+C로 종료")
    print(f"{'='*60}\n")

    # Initial snapshot
    mtimes: dict[str, float] = {}
    for pyfile in dirpath.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue
        mtimes[str(pyfile)] = pyfile.stat().st_mtime

    # Initial full scan
    findings = scan_directory(dirpath)
    critical = report(findings, "초기 전체 스캔")
    if critical:
        print(f"\n  ⚠ {critical}건의 긴급 보안 이슈 — 즉시 수정 필요")

    print(f"\n  감시 시작... ({datetime.now().strftime('%H:%M:%S')})")

    try:
        while True:
            time.sleep(interval)
            changed: list[Path] = []

            for pyfile in dirpath.rglob("*.py"):
                if "__pycache__" in str(pyfile):
                    continue
                key = str(pyfile)
                mtime = pyfile.stat().st_mtime

                if key not in mtimes:
                    # New file
                    mtimes[key] = mtime
                    changed.append(pyfile)
                    print(f"  📄 새 파일: {pyfile.name}")
                elif mtime > mtimes[key]:
                    # Modified file
                    mtimes[key] = mtime
                    changed.append(pyfile)
                    print(f"  ✏️ 변경 감지: {pyfile.name} ({datetime.now().strftime('%H:%M:%S')})")

            if changed:
                for f in changed:
                    file_findings = scan_file(f)
                    ruff_findings = asyncio.run(run_ruff_security(f))
                    all_f = file_findings + ruff_findings
                    critical = report(all_f, f"변경 스캔: {f.name}")
                    if critical:
                        print(f"  🚨 {f.name} — {critical}건 위협! 즉시 확인 필요")

    except KeyboardInterrupt:
        print(f"\n\n  경비 에이전트 종료 ({datetime.now().strftime('%H:%M:%S')})")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(
        description="경비(警備) 에이전트 — 프로젝트 보안 상시 감시",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--watch", type=str, default="", help="디렉토리 상시 감시")
    p.add_argument("--scan", type=str, default="", help="단일 파일 보안 스캔")
    p.add_argument("--scan-all", type=str, default="", help="디렉토리 전체 스캔")
    p.add_argument("--star-chamber", type=str, default="", help="다중모델 보안 리뷰")
    p.add_argument("--interval", type=float, default=5.0, help="감시 간격 (초, 기본 5)")

    args = p.parse_args()

    if args.scan:
        path = Path(args.scan)
        if not path.exists():
            print(f"[오류] 파일 없음: {path}")
            sys.exit(1)
        findings = scan_file(path)
        ruff_f = asyncio.run(run_ruff_security(path))
        critical = report(findings + ruff_f, f"보안 스캔: {path.name}")
        sys.exit(1 if critical else 0)

    elif args.scan_all:
        path = Path(args.scan_all)
        if not path.is_dir():
            print(f"[오류] 디렉토리 없음: {path}")
            sys.exit(1)
        findings = scan_directory(path)
        critical = report(findings, f"전체 스캔: {path.name}")
        sys.exit(1 if critical else 0)

    elif args.star_chamber:
        path = Path(args.star_chamber)
        if not path.exists():
            print(f"[오류] 파일 없음: {path}")
            sys.exit(1)
        # Pattern scan + Star Chamber
        findings = scan_file(path)
        ruff_f = asyncio.run(run_ruff_security(path))
        report(findings + ruff_f, f"패턴 스캔: {path.name}")
        asyncio.run(star_chamber_security_review(path))

    elif args.watch:
        path = Path(args.watch)
        if not path.is_dir():
            print(f"[오류] 디렉토리 없음: {path}")
            sys.exit(1)
        watch_directory(path, args.interval)

    else:
        # Default: scan current directory
        watch_directory(Path("."), args.interval)


if __name__ == "__main__":
    main()
