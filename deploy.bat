@echo off
REM Deploy and monitor FCC Radio License Map (Windows version)

setlocal enabledelayedexpansion

REM Configuration
set DOCKER_COMPOSE=docker compose
set TIMEOUT_SECONDS=300

REM Check prerequisites
echo Checking prerequisites...

where docker >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker not found. Please install Docker Desktop.
    exit /b 1
)

where docker-compose >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    docker compose version >nul 2>nul
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Docker Compose not found.
        exit /b 1
    )
)

echo [OK] Prerequisites check passed

REM Check .env file
if not exist .env (
    if exist .env.example (
        echo [WARNING] .env file not found. Creating from .env.example...
        copy .env.example .env
        echo [INFO] Please edit .env with your production settings
    ) else (
        echo [ERROR] .env.example not found
        exit /b 1
    )
) else (
    echo [OK] .env file exists
)

REM Parse command line arguments
if "%1"=="" goto :usage
if /i "%1"=="deploy" goto :deploy
if /i "%1"=="check" goto :check
if /i "%1"=="import" goto :import
if /i "%1"=="logs" goto :logs
goto :usage

:deploy
echo Starting deployment...
echo Stopping existing containers...
%DOCKER_COMPOSE% down --remove-orphans 2>nul

echo Building and starting containers...
%DOCKER_COMPOSE% up -d --build
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start containers
    exit /b 1
)

echo Waiting for services to become healthy...
timeout /t 10 /nobreak

REM Check if data exists
for /f %%i in ('%DOCKER_COMPOSE% exec -T db psql -U fcc -d fcc -t -c "SELECT COUNT(*) FROM license;" 2^>nul') do set COUNT=%%i
if "!COUNT!"=="" set COUNT=0

if !COUNT! equ 0 (
    echo [WARNING] No license data found
    set /p IMPORT="Import FCC data now? (y/n) "
    if /i "!IMPORT!"=="y" goto :import
)

call :verify_deployment
echo.
echo [OK] Deployment complete!
echo Open http://localhost:8000 in your browser
goto :end

:check
echo Checking deployment status...
echo.
echo Container status:
%DOCKER_COMPOSE% ps
echo.

for /f %%i in ('%DOCKER_COMPOSE% exec -T web curl -s http://localhost:8000/api/health') do (
    echo [INFO] Health check: %%i
)
goto :end

:import
echo Starting FCC data import...
echo This may take 30-60 minutes. Please wait...
%DOCKER_COMPOSE% exec web python -m app.cli import-full
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Import failed
    exit /b 1
)
echo [OK] Import complete
goto :end

:logs
echo Displaying recent logs (Press Ctrl+C to exit)...
%DOCKER_COMPOSE% logs -f --tail=50
goto :end

:verify_deployment
echo Verifying deployment...
%DOCKER_COMPOSE% ps
goto :eof

:usage
echo FCC Radio License Map - Production Deployment
echo.
echo Usage: %0 COMMAND
echo.
echo Commands:
echo   deploy    - Full deployment (build, start, verify^)
echo   check     - Verify deployment status
echo   import    - Import FCC data
echo   logs      - View application logs
echo.
echo Example:
echo   %0 deploy     [First time deployment]
echo   %0 check      [Verify deployment is healthy]
echo   %0 import     [Import data manually]
echo.
exit /b 1

:end

