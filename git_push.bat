@echo off
setlocal

echo ==========================================
echo MES - Push current PC changes to GitHub
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
    echo Run git_first_setup_MES.bat first.
    pause
    exit /b 1
)

echo [1/4] Checking status...
git status --short

echo.
echo [2/4] Adding changes...
git add .
if errorlevel 1 goto :error

echo [3/4] Creating commit...
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Sync changes"
    if errorlevel 1 goto :error
) else (
    echo No changes to commit.
)

echo [4/4] Pushing to GitHub...
git push
if errorlevel 1 goto :error

echo.
echo SUCCESS: Push completed.
pause
exit /b 0

:error
echo.
echo ERROR: Push failed.
pause
exit /b 1
