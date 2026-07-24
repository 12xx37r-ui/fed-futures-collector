@echo off
setlocal
cd /d "%~dp0"
echo Fed Policy Engine V3.3 patch
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 patch_v3_3.py
) else (
  python patch_v3_3.py
)
echo.
pause
