FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIMBER_DB=/data/timber.db

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system --gid 10001 timber \
    && useradd --system --uid 10001 --gid timber --home-dir /app timber \
    && mkdir -p /data \
    && chown timber:timber /data

COPY --chown=timber:timber backend ./backend
COPY --chown=timber:timber requirements.txt ./requirements.txt

USER timber:timber

EXPOSE 8000

CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
