#!/bin/sh
# Bind to the pod/container's own IP, never to 0.0.0.0.
# In Kubernetes the primary NIC IP is available as MY_POD_IP (injected by the Downward API).
# For docker-compose or direct Docker runs use the first non-loopback address as fallback.
set -eu

BIND_IP="${MY_POD_IP:-}"
if [ -z "$BIND_IP" ]; then
  # First non-loopback IPv4 address
  BIND_IP="$(ip -4 addr show scope global | awk '/inet / {print $2}' | cut -d/ -f1 | head -1)"
fi
: "${BIND_IP:=127.0.0.1}"   # final fallback: loopback if nothing else available

export FINOPS_HOST="$BIND_IP"

exec .venv/bin/uvicorn app.main:app_factory \
  --factory \
  --host "$BIND_IP" \
  --port "${FINOPS_PORT:-8080}" \
  --workers "${FINOPS_WORKERS:-2}"
