# One small image, one process, no build step for the front end.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a source change does not re-resolve them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY static ./static
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
COPY pytest.ini README.md ./

# Cloud Run injects PORT; locally it falls back to 8080. One worker: the session
# lives in the browser, so there is no shared state to synchronise and a single
# process keeps p50 latency flat.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level warning
