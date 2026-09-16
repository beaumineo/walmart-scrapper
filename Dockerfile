FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WALMART_COLLECT_INLINE=1

COPY requirements.txt requirements-railway.txt BUILD.txt ./
RUN pip install --no-cache-dir -r requirements-railway.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts

WORKDIR /app/app
EXPOSE 8000
# Shell form so Railway's $PORT is honored (often 8080)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
