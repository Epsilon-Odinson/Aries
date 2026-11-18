# Aries production image — single service.
# Stage 1 builds the React frontend; stage 2 runs FastAPI and serves
# the built frontend as static files, so Render runs ONE web service
# (no CORS, no second service to keep alive).
FROM node:20-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app/server
# OpenCV headless needs libGL-free system libs; install the small set it needs.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
COPY server/requirements.txt ./
RUN pip install -r requirements.txt
COPY server/ ./
COPY --from=web-build /build/web/dist ./static
# Render injects $PORT; uvicorn binds it. Data dir holds SQLite + faces.
RUN mkdir -p data/faces
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
