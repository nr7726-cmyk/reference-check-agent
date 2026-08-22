FROM node:20-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ARG INSTALL_AI_RUNTIME=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/static \
    COPILOT_SKIP_CLI_DOWNLOAD=1 \
    COPILOT_HOME=/home/app/copilot \
    PORT=8000
WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app
COPY backend/pyproject.toml /tmp/backend/
COPY backend/app/ /tmp/backend/app/
RUN if [ "$INSTALL_AI_RUNTIME" = "true" ]; then \
      pip install --no-cache-dir "/tmp/backend[ai]" && \
      runtime_path="$(python -c 'import pathlib, copilot; print(pathlib.Path(copilot.__file__).parent / "bin" / "copilot")')" && \
      chmod 755 "$runtime_path" && \
      mkdir -p /opt/copilot && ln -s "$runtime_path" /opt/copilot/copilot; \
    else \
      pip install --no-cache-dir /tmp/backend; \
    fi && rm -rf /tmp/backend
COPY --from=frontend-build /build/frontend/dist/ /app/static/

USER app
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
