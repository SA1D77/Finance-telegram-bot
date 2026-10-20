@echo off
chcp 65001 >nul
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
    echo Docker не найден. Установите Docker Desktop: https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
    echo Docker Desktop не запущен. Откройте Docker Desktop, дождитесь зелёного статуса и запустите этот файл снова.
    pause
    exit /b 1
)

if not exist "src\.env" (
    echo Нет файла src\.env. Скопируйте src\.env.example в src\.env и заполните его.
    pause
    exit /b 1
)

findstr /r /c:"^TELEGRAM=..*" "src\.env" >nul || (
    echo В src\.env не заполнен TELEGRAM ^(токен бота от @BotFather^).
    pause
    exit /b 1
)
findstr /r /c:"^OWNER_ID=[0-9][0-9]*" "src\.env" >nul || (
    echo В src\.env не заполнен OWNER_ID ^(ваш числовой ID от @userinfobot^).
    pause
    exit /b 1
)

rem Optional offline recognisers are built into the image only when switched on in src\.env.
set INSTALL_VOICE=0
set INSTALL_RECEIPTS=0
findstr /r /c:"^FEATURE_VOICE=1" "src\.env" >nul && set INSTALL_VOICE=1
findstr /r /c:"^FEATURE_RECEIPTS=1" "src\.env" >nul && set INSTALL_RECEIPTS=1

echo Запускаю бота...
docker compose up -d --build
if errorlevel 1 (
    echo Не получилось запустить. Подробности выше.
    pause
    exit /b 1
)

echo.
echo Бот запущен. Напишите ему /start в Telegram.
echo Логи: docker compose logs -f app      Остановить: stop.bat
pause
