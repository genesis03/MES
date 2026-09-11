@echo off
setlocal

echo ==========================================
echo MES - Pull latest changes from GitHub
echo ==========================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or not in PATH.
    pause
    exit /b 1
)

if not exist ".git" (
    echo ERROR: This folder is not a Git repository.
    echo On a new PC, use:
    echo git clone https://github.com/genesis03/MES.git
    pause
    exit /b 1
)

for /f "delims=" %%i in ('git status --porcelain') do (
    echo ERROR: Uncommitted local changes exist.
    echo Commit or push them before pulling.
    echo.
    git status --short
    pause
    exit /b 1
)

echo [1/3] Fetching...
git fetch origin
if errorlevel 1 goto :error

echo [2/3] Pulling latest main branch...
git pull --rebase origin main
if errorlevel 1 goto :error

echo [3/3] Current status:
git status

echo.
echo SUCCESS: Pull completed.
pause
exit /b 0

:error
echo.
echo ERROR: Pull failed.
pause
exit /b 1
