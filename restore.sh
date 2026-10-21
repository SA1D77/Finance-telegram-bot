#!/usr/bin/env bash
# Restores a backup made by the bot (backups/finance-....sql.gz), replacing the current data.
set -euo pipefail
cd "$(dirname "$0")"

file="${1:-}"
if [ -z "$file" ] || [ ! -f "$file" ]; then
    echo "Использование: ./restore.sh backups/finance-ГГГГ-ММ-ДД_ЧЧММСС.sql.gz"
    exit 1
fi

echo "ВНИМАНИЕ: текущие данные бота будут заменены данными из копии $file"
read -r -p "Продолжить? [y/N] " answer
[ "$answer" = "y" ] || [ "$answer" = "Y" ] || exit 0

# Stop everything that writes to the database, restore, start again.
docker compose stop app celery beat
docker compose up -d --wait db
gunzip -c "$file" | docker compose exec -T db sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose up -d
echo "Готово: данные восстановлены, бот запущен."
