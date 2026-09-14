#!/bin/sh
set -eu

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="/backups/stage15-${POSTGRES_DB}-${timestamp}.dump"
pg_dump --format=custom --no-owner --no-privileges \
  --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --file="$target"
sha256sum "$target" > "$target.sha256"
bytes="$(wc -c < "$target" | tr -d ' ')"
printf '{"backup":"%s","bytes":%s}\n' "$target" "$bytes"
