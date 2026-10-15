@echo off
chcp 65001 >nul
cd /d %~dp0
"C:\Users\fengz\.workbuddy\binaries\python\envs\default\Scripts\python.exe" scrape_news.py
