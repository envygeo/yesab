@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
uv run python "%SCRIPT_DIR%scripts\run_datasette_explorer.py" %*
exit /b %ERRORLEVEL%
