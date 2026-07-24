@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo Fed Policy Engine V3.2 FRED 수정
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 patch_v3_2.py
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python patch_v3_2.py
    ) else (
        echo [실패] Python을 찾지 못했습니다.
        echo GitHub Actions 프로젝트에서 Python을 사용하더라도,
        echo 현재 Windows에는 Python이 설치되지 않았을 수 있습니다.
        echo.
        pause
        exit /b 1
    )
)

echo.
pause
