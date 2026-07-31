@echo off
chcp 65001 >nul
rem v1.1 2026-07-31 服務已在跑時自動關窗（只有出錯才 pause），供 mediatools:// protocol 呼叫
rem v1.0 2026-07-31 啟動影音工具本機服務（port 8767）
cd /d "%~dp0"
"C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe" server.py
if errorlevel 1 pause