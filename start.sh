#!/usr/bin/env bash
# Linux / Raspberry Pi: builds and starts the bot (the same as start.bat on Windows).
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не найден. Установите: curl -fsSL https://get.docker.com | sh"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Нет доступа к Docker. Выполните: sudo usermod -aG docker \$USER, затем выйдите и войдите снова (или перезагрузитесь)."
    exit 1
fi

if [ ! -f src/.env ]; then
    # First run: a settings file with fresh random passwords; only the bot token and your id are left.
    pg=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
    rd=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
    sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pg}|" \
        -e "s|^DB_URL=.*|DB_URL=postgresql+asyncpg://finance:${pg}@db:5432/finance|" \
        -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${rd}|" \
        src/.env.example > src/.env
    chmod 600 src/.env
    echo "Создан src/.env с паролями. Впишите в него TELEGRAM= и OWNER_ID= (nano src/.env) и запустите ./start.sh снова."
    exit 1
fi

if ! grep -qE '^TELEGRAM=.+' src/.env; then
    echo "В src/.env не заполнен TELEGRAM (токен бота от @BotFather)."
    exit 1
fi
if ! grep -qE '^OWNER_ID=[0-9]+' src/.env; then
    echo "В src/.env не заполнен OWNER_ID (ваш числовой ID от @userinfobot)."
    exit 1
fi

# Optional offline recognisers are built into the image only when switched on in src/.env.
export INSTALL_VOICE=0 INSTALL_RECEIPTS=0
grep -qE '^FEATURE_VOICE=1' src/.env && export INSTALL_VOICE=1
grep -qE '^FEATURE_RECEIPTS=1' src/.env && export INSTALL_RECEIPTS=1

echo "Запускаю бота..."
docker compose up -d --build
echo
echo "Бот запущен и будет сам стартовать после перезагрузки. Напишите ему /start в Telegram."
echo "Логи: docker compose logs -f app      Остановить: ./stop.sh"
