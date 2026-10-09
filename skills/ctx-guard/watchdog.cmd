@echo off
REM ============================================================
REM  ctx-guard watchdog 启动器
REM  用法:  watchdog.cmd "C:\你的\项目目录"
REM  不传参数则用当前目录。安全绳: 最多自动重启 10 次。
REM ============================================================
setlocal
set "WATCH_DIR=%~1"
if "%WATCH_DIR%"=="" set "WATCH_DIR=%CD%"
start "ctx-guard watchdog" /min python -I "%~dp0scripts\watchdog.py" ^
  --dir "%WATCH_DIR%" --threshold 82 --poll 20 --max-restarts 10
echo watchdog started for: %WATCH_DIR%
echo log: %WATCH_DIR%\.ctxguard-watchdog.log
echo safety rope: max 10 auto-restarts
timeout /t 6
