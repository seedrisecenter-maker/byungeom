# 변검(變臉) Star Chamber API — Pricing

> Claude, Gemini, GPT가 동시에 코드를 심문합니다.
> 세 모델이 합의한 문제만이 진짜 문제입니다.

---

## Plans

| Plan       | Price              | Reviews/day | SLA          |
|------------|--------------------|-------------|--------------|
| Free       | 무료               | 5           | Best-effort  |
| Pro        | 50,000 KRW / month | Unlimited   | 99.5% uptime |
| Enterprise | 별도 협의          | Unlimited   | Custom SLA + dedicated instance |

---

## Free Tier

- **5 reviews per day** (resets at midnight UTC)
- All three model panels included (Claude + Gemini + GPT)
- Full JSON response with consensus/majority/individual issue classification
- No credit card required

```
X-API-Key: byungeom-demo-free-key
```

---

## Pro — 50,000 KRW / month

- Unlimited reviews per day
- Priority queue (requests skip the rate-limit debounce)
- Usage dashboard via GET /usage
- Email support
- Cancel anytime

**결제 방법**: 월정액 계좌이체 또는 카드 결제 (신청 후 안내)

---

## Enterprise

- Dedicated instance (Docker / Railway / Fly.io)
- Custom model panel (add proprietary LLMs)
- On-premise deployment option
- SLA 보장 + 전담 지원
- 1-hour response for critical issues

**문의**: seedrisecenter@gmail.com

---

## API Quick Start

```bash
# 1. Review a code snippet
curl -X POST https://your-api-host/review \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{
    "code": "def add(a, b): return a + b",
    "goal": "Add two numbers and return the result"
  }'
```

```json
// Example response
{
  "request_id": "a3f9b1c2",
  "worst_verdict": "ALIVE",
  "participating_models": ["claude-sonnet", "gemini-2.5-flash", "gpt-4o-mini"],
  "consensus_issues": [],
  "majority_issues": ["No type hints — callers cannot infer expected types"],
  "individual_issues": ["Missing docstring"],
  "reviews": [...],
  "duration_ms": 3421
}
```

```bash
# 2. Check remaining quota
curl https://your-api-host/usage \
  -H "X-API-Key: YOUR_KEY"
```

---

## Verdict Legend

| Verdict  | Meaning                                      |
|----------|----------------------------------------------|
| ALIVE    | Code is acceptable — minor issues only       |
| WOUNDED  | Significant problems found, fix recommended  |
| DEAD     | Critical issues — do not ship                |
| UNKNOWN  | Model unavailable or parse error             |

Worst verdict across all participating models is returned as `worst_verdict`.

---

## Issue Classification

Issues are clustered by keyword overlap across reviewers:

- **consensus_issues** — all available models flagged the same problem
- **majority_issues** — 2+ models agreed
- **individual_issues** — only one model flagged (lower confidence)

---

## Limits & Fair Use

| Limit             | Free    | Pro / Enterprise |
|-------------------|---------|------------------|
| Max code length   | 20,000 chars | 20,000 chars |
| Max goal length   | 500 chars    | 500 chars    |
| Debounce (per IP) | 2 sec        | 2 sec        |
| Daily reviews     | 5            | Unlimited    |

---

*변검 Star Chamber is powered by the open-source `byungeom` library.*
*Apache 2.0 — https://github.com/seedrisecenter-maker/byungeom*
