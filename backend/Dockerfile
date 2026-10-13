FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock requirements-dev.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . .
RUN pip install --no-cache-dir --no-deps .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS dev
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock

FROM base AS prod
