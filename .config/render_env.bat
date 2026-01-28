@echo off
REM Render .env files from templates
REM Usage: render_env.bat [dev|prod] [--skip-validation]
REM
REM This script uses uv to run render_env.py with inline dependencies.
REM Dependencies are installed in an isolated cache (NOT global environment).

cd /d "%~dp0"

REM Check if uv is available
where uv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Error: uv is not installed or not in PATH
    echo Install it from: https://docs.astral.sh/uv/
    exit /b 1
)

REM Run the script with uv (handles inline dependencies automatically)
uv run render_env.py %*
