#!/bin/sh
set -eu

backup_name="${1:-}"
target_db="${2:-zhishu_stage15_restore}"
case "$backup_name" in
  stage15-*.dump) ;;
  *) echo "refusing: backup basename must match stage15-*.dump" >&2; exit 2 ;;
esac
case "$target_db" in
  *stage15*restore*) ;;
  *) echo "refusing: target database must contain stage15 and restore" >&2; exit 2 ;;
esac
test -f "/backups/$backup_name"
sha256sum -c "/backups/$backup_name.sha256"
dropdb --if-exists --force --username="$POSTGRES_USER" "$target_db"
createdb --username="$POSTGRES_USER" "$target_db"
pg_restore --no-owner --no-privileges --username="$POSTGRES_USER" \
  --dbname="$target_db" "/backups/$backup_name"
printf '{"restored_database":"%s","source":"%s"}\n' "$target_db" "$backup_name"
