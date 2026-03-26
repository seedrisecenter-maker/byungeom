"""
변검(變臉) Star Chamber — Paid API Server

POST /review   : run multi-model code review
GET  /usage    : check remaining quota
POST /admin/keys : create new API key (admin only)

Tiers:
  free  — 5 reviews / day
  pro   — unlimited (50,000 KRW/month)
  admin — internal, unlimited
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "byungeom_api.db"))

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "change-me-in-production")

FREE_DAILY_LIMIT = 5
MAX_CODE_LENGTH = 20_000   # characters
MAX_GOAL_LENGTH = 500

log = logging.getLogger("byungeom_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash    TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                tier        TEXT NOT NULL DEFAULT 'free',
                created_at  TEXT NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS usage_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key_hash    TEXT NOT NULL,
                ts          TEXT NOT NULL,
                day         TEXT NOT NULL,
                duration_ms INTEGER,
                verdict     TEXT,
                model_count INTEGER,
                ip          TEXT,
                FOREIGN KEY(key_hash) REFERENCES api_keys(key_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_log(key_hash, day);
        """)
        log.info("DB initialised at %s", DB_PATH)


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_key_row(key_hash: str) -> sqlite3.Row | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ).fetchone()
    return row


def _count_today(key_hash: str) -> int:
    today = date.today().isoformat()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM usage_log WHERE key_hash = ? AND day = ?",
            (key_hash, today),
        ).fetchone()
    return row["cnt"] if row else 0


def _log_usage(
    key_hash: str,
    duration_ms: int,
    verdict: str,
    model_count: int,
    ip: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO usage_log (key_hash, ts, day, duration_ms, verdict, model_count, ip)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key_hash, now, today, duration_ms, verdict, model_count, ip),
        )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    code: str = Field(..., max_length=MAX_CODE_LENGTH, description="Source code to review")
    goal: str = Field(..., max_length=MAX_GOAL_LENGTH, description="What the code is supposed to do")
    include_gemini: bool = Field(True, description="Include Gemini in the panel")
    include_gpt: bool = Field(True, description="Include GPT in the panel")


class ModelReviewOut(BaseModel):
    model_name: str
    verdict: str
    attacks: list[str]
    feedback: str
    severity: int
    error: str


class ReviewResponse(BaseModel):
    request_id: str
    worst_verdict: str
    participating_models: list[str]
    consensus_issues: list[str]
    majority_issues: list[str]
    individual_issues: list[str]
    reviews: list[ModelReviewOut]
    duration_ms: int


class UsageResponse(BaseModel):
    tier: str
    label: str
    reviews_today: int
    daily_limit: int | None    # None = unlimited


class CreateKeyRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    tier: str = Field("free", pattern="^(free|pro|admin)$")


class CreateKeyResponse(BaseModel):
    api_key: str    # shown only once
    label: str
    tier: str


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> sqlite3.Row:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    key_row = _get_key_row(_hash_key(x_api_key))
    if not key_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
        )
    return key_row


async def require_admin(
    x_admin_secret: Annotated[str | None, Header(alias="X-Admin-Secret")] = None,
) -> None:
    if not x_admin_secret or not secrets.compare_digest(x_admin_secret, ADMIN_SECRET):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


# ---------------------------------------------------------------------------
# Rate limiter (in-memory per-IP guard + DB quota check)
# ---------------------------------------------------------------------------

_ip_lock: dict[str, float] = {}   # ip -> last request time (debounce)
_DEBOUNCE_SEC = 2.0


def _check_quota(key_row: sqlite3.Row) -> None:
    """Raise 429 if the free tier has exceeded its daily limit."""
    if key_row["tier"] in ("pro", "admin"):
        return
    used = _count_today(key_row["key_hash"])
    if used >= FREE_DAILY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Free tier limit reached ({FREE_DAILY_LIMIT} reviews/day). "
                "Upgrade to Pro at https://byungeom.ai/pricing"
            ),
        )


def _check_debounce(ip: str) -> None:
    """Prevent hammering: same IP can't submit more than once every 2s."""
    last = _ip_lock.get(ip, 0.0)
    now = time.monotonic()
    if now - last < _DEBOUNCE_SEC:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests — wait a moment before retrying",
        )
    _ip_lock[ip] = now


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    # Create a default free demo key if the DB is empty
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"]
        if count == 0:
            demo_key = "byungeom-demo-free-key"
            conn.execute(
                "INSERT INTO api_keys (key_hash, label, tier, created_at) VALUES (?, ?, ?, ?)",
                (_hash_key(demo_key), "demo", "free", datetime.now(timezone.utc).isoformat()),
            )
            log.info("Created default demo key: %s", demo_key)
    yield


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="변검(變臉) Star Chamber API",
    description=(
        "Multi-model parallel code review powered by Claude, Gemini, and GPT. "
        "Models debate independently; issues are classified by consensus level."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe — returns 200 if the server is up."""
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/review", response_model=ReviewResponse, tags=["review"])
async def post_review(
    body: ReviewRequest,
    request: Request,
    key_row: Annotated[sqlite3.Row, Depends(require_api_key)],
) -> ReviewResponse:
    """
    Run a Star Chamber code review.

    - **code**: full source code (max 20 000 chars)
    - **goal**: what the code is supposed to achieve (max 500 chars)
    - Returns consensus issues classified by how many models agreed.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_debounce(client_ip)
    _check_quota(key_row)

    # Import here to avoid import-time failures if byungeom isn't installed
    try:
        from byungeom import StarChamber
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"byungeom package not installed on this server: {exc}",
        ) from exc

    sc = StarChamber(
        include_gemini=body.include_gemini,
        include_gpt=body.include_gpt,
    )

    t0 = time.monotonic()
    try:
        result = await sc.review(body.code, body.goal)
    except Exception as exc:
        log.exception("StarChamber.review failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Review engine error: {exc}",
        ) from exc

    duration_ms = int((time.monotonic() - t0) * 1000)

    _log_usage(
        key_hash=key_row["key_hash"],
        duration_ms=duration_ms,
        verdict=result.worst_verdict,
        model_count=len(result.participating_models),
        ip=client_ip,
    )

    request_id = secrets.token_hex(8)

    return ReviewResponse(
        request_id=request_id,
        worst_verdict=result.worst_verdict,
        participating_models=result.participating_models,
        consensus_issues=result.consensus_issues,
        majority_issues=result.majority_issues,
        individual_issues=result.individual_issues,
        reviews=[
            ModelReviewOut(
                model_name=r.model_name,
                verdict=r.verdict,
                attacks=r.attacks,
                feedback=r.feedback,
                severity=r.severity,
                error=r.error,
            )
            for r in result.reviews
        ],
        duration_ms=duration_ms,
    )


@app.get("/usage", response_model=UsageResponse, tags=["account"])
async def get_usage(
    key_row: Annotated[sqlite3.Row, Depends(require_api_key)],
) -> UsageResponse:
    """Return today's usage count and remaining quota for the authenticated key."""
    tier = key_row["tier"]
    used = _count_today(key_row["key_hash"])
    return UsageResponse(
        tier=tier,
        label=key_row["label"],
        reviews_today=used,
        daily_limit=FREE_DAILY_LIMIT if tier == "free" else None,
    )


@app.post("/admin/keys", response_model=CreateKeyResponse, tags=["admin"])
async def create_key(
    body: CreateKeyRequest,
    _: Annotated[None, Depends(require_admin)],
) -> CreateKeyResponse:
    """
    Create a new API key.  Requires the X-Admin-Secret header.

    The raw key is returned **only once** — store it securely.
    """
    raw_key = f"byk-{secrets.token_urlsafe(32)}"
    key_hash = _hash_key(raw_key)
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO api_keys (key_hash, label, tier, created_at) VALUES (?, ?, ?, ?)",
                (key_hash, body.label, body.tier, now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Key collision — retry",
            )

    log.info("Created API key: label=%s tier=%s", body.label, body.tier)
    return CreateKeyResponse(api_key=raw_key, label=body.label, tier=body.tier)


@app.delete("/admin/keys/{label}", tags=["admin"])
async def deactivate_key(
    label: str,
    _: Annotated[None, Depends(require_admin)],
) -> dict:
    """Deactivate all keys with the given label."""
    with _get_conn() as conn:
        result = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE label = ?", (label,)
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="No keys found for that label")
    return {"deactivated": result.rowcount, "label": label}


@app.get("/admin/stats", tags=["admin"])
async def get_stats(
    _: Annotated[None, Depends(require_admin)],
) -> dict:
    """Return aggregate usage stats by tier and day."""
    with _get_conn() as conn:
        daily = conn.execute("""
            SELECT u.day, k.tier, COUNT(*) AS reviews,
                   AVG(u.duration_ms) AS avg_ms
            FROM usage_log u
            JOIN api_keys k ON k.key_hash = u.key_hash
            GROUP BY u.day, k.tier
            ORDER BY u.day DESC
            LIMIT 60
        """).fetchall()
        keys = conn.execute("""
            SELECT tier, COUNT(*) AS total,
                   SUM(is_active) AS active
            FROM api_keys GROUP BY tier
        """).fetchall()

    return {
        "daily_usage": [dict(row) for row in daily],
        "key_counts": [dict(row) for row in keys],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
        log_level="info",
    )
