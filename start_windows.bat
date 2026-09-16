@echo off
setlocal
cd /d "%~dp0"
where docker >nul 2>nul
if errorlevel 1 goto nodocker
if not exist .env (
  py scripts\configure.py
  if errorlevel 1 goto error
)
docker compose up -d db
if errorlevel 1 goto error
docker compose build web
if errorlevel 1 goto error
docker compose run --rm web alembic upgrade head
if errorlevel 1 goto error
if /I "%~1"=="--seed" (
  docker compose run --rm web flask --app wsgi seed-demo
  if errorlevel 1 goto error
)
docker compose up -d web
if errorlevel 1 goto error
echo Open http://localhost:8000
echo First run only: start_windows.bat --seed
pause
exit /b 0
:nodocker
echo Docker Desktop is required for this launcher. See START_HERE.md for native PostgreSQL setup.
pause
exit /b 1
:error
echo Start failed. Read the message above and START_HERE.md.
pause
exit /b 1
