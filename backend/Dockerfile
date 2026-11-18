# AASTAR backend image — used for the API, the pipeline worker and the one-shot migrate job.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB_CONCURRENCY=2 \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

RUN useradd --system --uid 10001 --home /app aastar && chown -R aastar:aastar /app
USER aastar

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# Default: the API. The worker / migrate services override the command in docker-compose.
# Gunicorn reads WEB_CONCURRENCY (workers) and FORWARDED_ALLOW_IPS (trusted proxy, i.e. the Nginx that
# sets X-Forwarded-For) from the environment. The default trusts only loopback, so a forgotten setting can never
# let callers spoof their address; servers set the frontend host's IP, the local compose stack sets "*".
# 2 workers x (DB_POOL_SIZE 4 + DB_MAX_OVERFLOW 2) + the worker's 6 = 18 connections (aastar_app limit 40).
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", \
     "--timeout", "120", "--access-logfile", "-"]
