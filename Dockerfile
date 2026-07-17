# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system jobagent \
    && useradd --system --gid jobagent --home-dir /app jobagent

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement requirements.txt

COPY alembic.ini run.py ./
COPY migrations ./migrations
COPY app ./app
COPY data ./data
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/var \
    && chown -R jobagent:jobagent /app/var

USER jobagent

ENV HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8080 \
    DATABASE_URL=sqlite+aiosqlite:////app/var/job_agent.db \
    DATABASE_AUTO_CREATE=false \
    RUN_DATABASE_MIGRATIONS=true \
    LOG_FILE_ENABLED=false

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port = os.getenv('PORT', os.getenv('HTTP_PORT', '8080')); urllib.request.urlopen(f'http://127.0.0.1:{port}/health/live', timeout=3).close()"]

STOPSIGNAL SIGTERM
ENTRYPOINT ["docker-entrypoint.sh"]
