@echo off
setlocal
chcp 65001 >nul
title 출하 바코드 관리 시스템 서버

echo ===================================================
echo  출하 바코드 관리 시스템 서버를 시작합니다.
echo  (외부 PC 접속 허용: 0.0.0.0:8000)
echo ===================================================
echo.

:: 이미 떠 있는 서버와 충돌한 상태를 새 서버 실행 성공으로 오인하지 않도록 중단합니다.
powershell.exe -NoProfile -Command "$mesPortPids = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if ($mesPortPids.Count -gt 0) { Write-Host ('[ERROR] Port 8000 is already in use. PID: ' + ($mesPortPids -join ', ')); exit 1 }; exit 0"
if errorlevel 1 (
    echo [오류] 8000번 포트를 사용하는 기존 서버를 종료한 뒤 다시 실행해 주세요.
    pause
    exit /b 1
)

:: 이 배치 파일이 있는 프로젝트에서 실행합니다.
cd /d "%~dp0"
if errorlevel 1 exit /b 1

:: 터미널의 가상환경 활성화 여부에 관계없이 프로젝트 Python을 우선합니다.
set "MES_SERVER_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "MES_SERVER_PYTHON=%CD%\.venv\Scripts\python.exe"
echo [서버 Python]
"%MES_SERVER_PYTHON%" -c "import sys; print(sys.executable)"
if errorlevel 1 (
    echo [오류] Python 실행 경로를 확인해 주세요.
    pause
    exit /b 1
)
echo.

:: 타 PC 접속을 위한 현재 서버 IP 주소 확인 및 출력
echo [현재 서버 PC의 IPv4 주소]
ipconfig | findstr /i "IPv4"
echo.
echo ---------------------------------------------------
echo  타 PC 접속 주소: http://[위 IPv4 주소]:8000/
echo ---------------------------------------------------
echo.

:: Uvicorn 웹 서버 가동 (외부 접속 허용 0.0.0.0)
:: 공유 폴더에서도 Python 변경을 감지하도록 파일 변경을 폴링합니다.
set "WATCHFILES_FORCE_POLLING=true"
"%MES_SERVER_PYTHON%" -m uvicorn app:app --app-dir "%CD%" --host 0.0.0.0 --port 8000 --reload --reload-dir "%CD%"

if %errorlevel% neq 0 (
    echo.
    echo [오류] 서버 실행 중 오류가 발생했습니다.
    pause
)
