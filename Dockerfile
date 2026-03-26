# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build tools
RUN pip install --no-cache-dir --upgrade pip wheel

# Copy package definition and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.29" \
    "pydantic>=2.0" \
    && pip install --no-cache-dir \
    "google-genai>=1.0" \
    "openai>=1.0" \
    || true     # optional — skip if pip can't resolve in build env


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for security
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Install the byungeom package from local source (editable install not possible in prod)
COPY byungeom/ ./byungeom/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.29" \
    "pydantic>=2.0"

# Copy application
COPY api_server.py .

# Data volume for SQLite persistence
RUN mkdir -p /data && chown app:app /data

# Override DB path to persistent volume
ENV DB_PATH=/data/byungeom_api.db
ENV PORT=8000

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["python", "-m", "uvicorn", "api_server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-access-log"]
