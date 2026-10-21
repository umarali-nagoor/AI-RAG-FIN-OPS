# ── Stage 1: build the React frontend ──────────────────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --omit=dev
COPY frontend/ ./
RUN npm run build          # output → ../backend/static (resolved inside container)
                           # vite.config.ts sets outDir: '../backend/static'

# ── Stage 2: build & run the Python backend ────────────────────────────────
FROM registry.redhat.io/ubi9/python-311-minimal:latest

# Create a non-root user (UID 1001 avoids conflict with most pre-existing users)
RUN useradd -m -u 1001 -s /sbin/nologin finops

WORKDIR /app

# Install Python dependencies into a venv owned by finops
COPY backend/requirements.txt ./
RUN /usr/bin/python3.11 -m venv .venv \
 && .venv/bin/pip install --no-cache-dir --upgrade pip \
 && .venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY backend/app ./app
COPY backend/config ./config
COPY backend/knowledge ./knowledge

# Copy built frontend assets from stage 1
COPY --from=frontend-build /app/backend/static ./static

# Copy and set up entrypoint
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
 && chown -R finops:finops /app

# Writable data directory for ChromaDB persistent store
RUN mkdir -p /data && chown finops:finops /data

USER 1001

ENV FINOPS_DATA_DIR=/data \
    FINOPS_ENVIRONMENT=production \
    FINOPS_LOG_LEVEL=INFO

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -sf "http://127.0.0.1:8080/healthz" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
