# Two targets from one codebase:
#   api    - FastAPI server (no browser), used by the `backend` service
#   worker - api + Google Chrome for BrowserFetcher (Facebook), used by `worker`
FROM python:3.12-slim AS api

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
# Apply migrations and load seed sources on start, then serve the API.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]


FROM api AS worker
# Google Chrome stable (a released browser, as people use it) plus its system libraries.
# libatomic1 is needed by Playwright's bundled Node driver on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && playwright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*
ENV BROWSER_CHANNEL=chrome
CMD ["python", "-m", "app.worker"]
