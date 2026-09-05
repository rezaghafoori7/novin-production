#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "سامانه مشترک ثبت تولید نوین روی پورت 8080 اجرا می‌شود"
echo "مرورگر: http://localhost:8080"
python3 server.py
