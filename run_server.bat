@echo off
chcp 65001 >nul
title 출하 바코드 관리 시스템 서버

echo ===================================================
echo  출하 바코드 관리 시스템 서버를 시작합니다.
echo  (외부 PC 접속 허용: 0.0.0.0:8000)
echo ===================================================
echo.

:: 프로젝트 작업 디렉터리로 이동
cd /d "C:\문서\공유폴더\BarcodeApp"

:: 타 PC 접속을 위한 현재 서버 IP 주소 확인 및 출력
echo [현재 서버 PC의 IPv4 주소]
ipconfig | findstr /i "IPv4"
echo.
echo ---------------------------------------------------
echo  타 PC 접속 주소: http://[위 IPv4 주소]:8000/
echo ---------------------------------------------------
echo.

:: Uvicorn 웹 서버 가동 (외부 접속 허용 0.0.0.0)
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

if %errorlevel% neq 0 (
    echo.
    echo [오류] 서버 실행 중 오류가 발생했습니다.
    pause
)