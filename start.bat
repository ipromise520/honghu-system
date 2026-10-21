@echo off
chcp 65001 >nul
title 洪湖支撑服务中心管理系统（本地版）
cd /d "%~dp0"

echo ============================================
echo   洪湖支撑服务中心 管理系统 - 本地运行版
echo   访问地址: http://127.0.0.1:8001
echo   演示账号: admin / admin123
echo ============================================
echo.

if not exist "backend\honghu_new.db" (
  echo [初始化] 首次运行，正在生成演示数据库（约需 1-2 分钟）...
)

cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

pause
