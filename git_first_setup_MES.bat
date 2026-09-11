@echo off
setlocal

set "REPO_URL=https://github.com/genesis03/MES.git"

echo ==========================================
echo MES GitHub Initial Setup
echo Repository: %REPO_URL%
echo ==========================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or not in PATH.
    echo Install Git for Windows first.
    pause
    exit /b 1
)

if not exist ".git" (
    echo [1/5] Initializing Git repository...
    git init
    if errorlevel 1 goto :error
) else (
    echo [1/5] Git repository already exists.
)

echo [2/5] Setting main branch...
git branch -M main
if errorlevel 1 goto :error

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo [3/5] Adding origin...
    git remote add origin "%REPO_URL%"
) else (
    echo [3/5] Updating origin...
    git remote set-url origin "%REPO_URL%"
)
if errorlevel 1 goto :error

echo [4/5] Adding and committing project files...
git add .
if errorlevel 1 goto :error

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Initial project sync"
    if errorlevel 1 goto :error
) else (
    echo No new files to commit.
)

echo [5/5] Pushing to GitHub...
git push -u origin main
if errorlevel 1 goto :push_error

echo.
echo ==========================================
echo SUCCESS: Initial sync completed.
echo ==========================================
pause
exit /b 0

:push_error
echo.
echo ERROR: GitHub push failed.
echo Possible causes:
echo - GitHub authentication is required.
echo - Remote repository already has commits.
echo - Repository permission issue.
echo.
echo If the remote repository already has files, try:
echo git pull origin main --rebase
echo.
pause
exit /b 1

:error
echo.
echo ERROR: Git command failed.
pause
exit /b 1
