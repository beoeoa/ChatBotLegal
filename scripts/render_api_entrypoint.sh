#!/bin/sh
# Render Blueprint supplies host:port values; compose deployments keep using
# their explicit URLs and the Dockerfile's default command.
set -eu

: "${SURREAL_HOSTPORT:?Render SurrealDB hostport is required}"
: "${LEGAL_HOSTPORT:?Render legal retrieval hostport is required}"
: "${FRONTEND_ORIGIN:?Render frontend origin is required}"
: "${API_PRIVATE_HOST:?Render API private hostname is required}"
: "${LEGAL_DATABASE_URL:?Render Postgres connection string is required}"
: "${OPEN_NOTEBOOK_ADMIN_PASSWORD:?Initial admin password is required}"
: "${OPEN_NOTEBOOK_ENCRYPTION_KEY:?Encryption key is required}"

export SURREAL_URL="ws://${SURREAL_HOSTPORT}/rpc"
export LEGAL_SEARCH_URL="http://${LEGAL_HOSTPORT}"
export LEGAL_SEARCH_URLS="${LEGAL_SEARCH_URL}"
export LEGAL_RETRIEVAL_V2_URL="${LEGAL_SEARCH_URL}"
export LEGAL_MANAGEMENT_URL="${LEGAL_SEARCH_URL}"
export CORS_ORIGINS="${FRONTEND_ORIGIN}"

frontend_host=${FRONTEND_ORIGIN#*://}
frontend_host=${frontend_host%%/*}
frontend_host=${frontend_host%%:*}
export TRUSTED_HOSTS="${API_PRIVATE_HOST},${frontend_host},localhost,127.0.0.1"

exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-5055}" --proxy-headers --forwarded-allow-ips '*'
