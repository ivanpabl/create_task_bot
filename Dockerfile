# ── Stage 1: Build frontend ─────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /build
COPY frontend/ .
RUN npm ci && npm run build

# ── Stage 2: Python runtime ─────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-builder /build/dist frontend/dist

# run.py (bot) or "uvicorn services.web.main:app --host 0.0.0.0 --port 8000" (api)
CMD ["python", "run.py"]
