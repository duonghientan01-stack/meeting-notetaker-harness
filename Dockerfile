# Container for Meeting Intelligence Harness (FastAPI + SQLite MVH)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# System dependencies for TLS and SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy harness code
COPY . /app/meeting-notetaker-harness

# Persistent volume directory for SQLite and raw email archive
RUN mkdir -p /app/meeting-notetaker-harness/data /app/meeting-notetaker-harness/data/raw_emails \
    && useradd -u 1000 -m appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["sh", "-c", "uvicorn meeting-notetaker-harness.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
