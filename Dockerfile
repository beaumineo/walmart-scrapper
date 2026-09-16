FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements.txt requirements-railway.txt ./
RUN pip install --no-cache-dir -r requirements-railway.txt

COPY app ./app
COPY data ./data

WORKDIR /app/app
EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
