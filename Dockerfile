FROM node:20-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/static \
    PORT=8000
WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/pyproject.toml /tmp/backend/
COPY backend/app/ /tmp/backend/app/
RUN pip install --no-cache-dir /tmp/backend && rm -rf /tmp/backend
COPY --from=frontend-build /build/frontend/dist/ /app/static/

USER app
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
