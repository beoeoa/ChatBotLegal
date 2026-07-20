#!/bin/sh
set -eu

seed=/seed/legal-corpus.dump

if [ ! -f "$seed" ]; then
  echo "No legal corpus seed found at $seed; starting with an empty legal database."
  exit 0
fi

echo "Restoring the packaged legal corpus."
pg_restore --exit-on-error --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$seed"
echo "Legal corpus restore completed."
