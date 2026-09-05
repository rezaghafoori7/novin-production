@echo off
chcp 65001 >nul
echo آدرس IPv4 این کامپیوتر را در بخش Ethernet یا Wi-Fi پیدا کنید:
echo.
ipconfig | findstr /i "IPv4"
echo.
echo نمونه آدرس ورود از کامپیوترهای دیگر:
echo http://192.168.1.10:8080
pause
