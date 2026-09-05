@echo off
chcp 65001 >nul
cd /d "%~dp0"
title سامانه مشترک ثبت تولید نوین
where python >nul 2>nul
if errorlevel 1 (
  echo پایتون روی این کامپیوتر نصب نیست.
  echo Python 3 را از python.org نصب کنید و گزینه Add Python to PATH را فعال کنید.
  pause
  exit /b 1
)
echo در حال اجرای سامانه روی پورت 8080 ...
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8080"
python server.py
pause
