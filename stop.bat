@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Stops the containers; the database stays in the Docker volume.
docker compose stop
echo Бот остановлен. Данные сохранены. Запустить снова: start.bat
pause
