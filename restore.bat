@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
    echo Использование: restore.bat backups\finance-ГГГГ-ММ-ДД_ЧЧММСС.sql.gz
    echo Можно перетащить файл копии мышкой на restore.bat.
    pause
    exit /b 1
)
if not exist "%~1" (
    echo Файл не найден: %~1
    pause
    exit /b 1
)

echo ВНИМАНИЕ: текущие данные бота будут заменены данными из копии
echo %~1
choice /c YN /m "Продолжить? Y - да, N - нет"
if errorlevel 2 exit /b 0

rem Stop everything that writes to the database, restore, start again.
docker compose stop app celery beat
docker compose up -d db
docker compose exec -T db sh -c "gunzip -c | psql -q -v ON_ERROR_STOP=1 -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\"" < "%~1"
if errorlevel 1 (
    echo Восстановление не удалось, подробности выше. Бот остановлен; запустите start.bat после исправления.
    pause
    exit /b 1
)
docker compose up -d
echo Готово: данные восстановлены, бот запущен.
pause
