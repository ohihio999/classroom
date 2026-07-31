@echo off
chcp 65001 >nul
rem v1.0 2026-07-31 啟動影音工具本機服務（port 8767），關閉視窗即停止
cd /d "%~dp0"
"C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe" server.py
pause