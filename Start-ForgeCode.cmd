@echo off
setlocal
title ForgeCode
cd /d "%~dp0"
set ELECTRON_RUN_AS_NODE=
set NODE_OPTIONS=
set NODE_PATH=
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js was not found. See docs\install\desktop-quickstart.md.
  if "%~1"=="" pause
  exit /b 1
)
node "%~dp0scripts\start_desktop.mjs" %*
if errorlevel 1 (
  if "%~1"=="" pause
  exit /b 1
)
exit /b 0
