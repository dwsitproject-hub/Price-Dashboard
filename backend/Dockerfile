# AASTAR backend image — used for the API, the pipeline worker and the one-shot migrate job.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

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
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "3", "-b", "0.0.0.0:8000", \
     "--forwarded-allow-ips", "*", "--timeout", "120", "--access-logfile", "-"]
