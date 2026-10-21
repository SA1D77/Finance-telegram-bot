#!/usr/bin/env bash
# Stops the containers; the database stays in the Docker volume.
set -euo pipefail
cd "$(dirname "$0")"
docker compose stop
echo "Бот остановлен. Данные сохранены. Запустить снова: ./start.sh"
