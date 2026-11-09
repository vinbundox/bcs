FROM node:22-alpine AS frontend-build

WORKDIR /app
COPY frontend/package.json ./package.json
COPY frontend/package-lock.json ./package-lock.json
RUN npm ci --no-audit --no-fund
COPY frontend/ ./frontend/
COPY public/ ./public/
WORKDIR /app/frontend
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIMBER_DB=/data/timber.db \
    TIMBER_FRONTEND_DIR=/app/frontend/dist

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system --gid 10001 timber \
    && useradd --system --uid 10001 --gid timber --home-dir /app timber \
    && mkdir -p /data \
    && chown timber:timber /data

COPY --chown=timber:timber backend ./backend
COPY --chown=timber:timber requirements.txt ./requirements.txt
COPY --from=frontend-build --chown=timber:timber /app/frontend/dist ./frontend/dist

USER timber:timber

EXPOSE 8000

CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && exec uvicorn backend.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
