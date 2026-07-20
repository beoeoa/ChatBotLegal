#!/bin/sh
# Bound dependency waiting so API/worker cannot enter a tight restart loop.

set -u

TIMEOUT_SECONDS="${SURREAL_WAIT_TIMEOUT_SECONDS:-120}"
INTERVAL_SECONDS="${SURREAL_WAIT_INTERVAL_SECONDS:-2}"
HEALTH_URL="${SURREAL_HEALTH_URL:-http://${SURREAL_HOST:-surrealdb}:${SURREAL_PORT:-8000}/health}"

case "$TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "ERROR: SURREAL_WAIT_TIMEOUT_SECONDS must be a positive integer" >&2
        exit 1
        ;;
esac

case "$INTERVAL_SECONDS" in
    ''|*[!0-9]*)
        echo "ERROR: SURREAL_WAIT_INTERVAL_SECONDS must be a positive integer" >&2
        exit 1
        ;;
esac

if [ "$TIMEOUT_SECONDS" -le 0 ] || [ "$INTERVAL_SECONDS" -le 0 ]; then
    echo "ERROR: SurrealDB wait values must be greater than zero" >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    echo "ERROR: wait-for-surreal.sh requires a child command" >&2
    exit 1
fi

echo "Waiting for SurrealDB dependency..."
elapsed=0
while [ "$elapsed" -lt "$TIMEOUT_SECONDS" ]; do
    if curl --connect-timeout 2 --max-time 3 --fail --silent "$HEALTH_URL" >/dev/null 2>&1; then
        echo "SurrealDB dependency is ready"
        exec "$@"
    fi
    sleep "$INTERVAL_SECONDS"
    elapsed=$((elapsed + INTERVAL_SECONDS))
done

echo "ERROR: SurrealDB dependency did not become ready within the configured timeout" >&2
exit 1

