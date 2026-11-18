# syntax=docker/dockerfile:1
# Образ агента: дашборд собирается Node.js на первом шаге, в итоговый образ попадает только статика.

FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Код читает документы, evals и сборку дашборда относительно корня проекта — поэтому структура
# каталогов как в репозитории и установка в режиме editable.
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/src backend/src
RUN pip install -e ./backend
COPY data data
COPY evals evals
COPY --from=ui /ui/dist frontend/dist

RUN useradd --create-home --uid 1000 dala && mkdir -p var && chown dala var
USER dala

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=40s --retries=5 \
  CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=2)" || exit 1

# При старте документы индексируются (неизменённые пропускаются), затем запускается API с дашбордом.
CMD ["sh", "-c", "python -m dala_agent.rag.indexer && exec uvicorn dala_agent.api.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
