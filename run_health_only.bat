@echo off
chcp 65001 >nul
cd /d "D:\Users\grok-auto-register"
python pool_health.py
python auto_link_cli.py
