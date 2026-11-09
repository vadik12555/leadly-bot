@echo off
chcp 65001 >nul
title Leadly bot
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 if not exist ".venv\Scripts\python.exe" (
  echo Python не найден. Установи Python 3.12 с python.org и отметь галочку "Add Python to PATH".
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: готовлю окружение...
  python -m venv .venv
)
echo Проверяю зависимости...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости. Проверь интернет и запусти файл ещё раз.
  pause
  exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul
findstr /r /c:"^BOT_TOKEN=..*" ".env" >nul
if errorlevel 1 (
  echo.
  echo Откроется файл настроек. Впиши BOT_TOKEN ^(из @BotFather^) и ADMIN_IDS ^(из @userinfobot^),
  echo сохрани Ctrl+S и закрой Блокнот.
  notepad ".env"
)

echo.
echo Запускаю бота. Пока это окно открыто, бот работает. Остановить: закрой окно или Ctrl+C.
echo.
".venv\Scripts\python.exe" -m leadly
echo.
echo Бот остановлен. Если выше ошибка, сделай скриншот этого окна.
pause
