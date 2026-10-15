@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_desktop.ps1"
if errorlevel 1 (
  echo.
  echo 桌面版启动失败。请按上方提示安装环境，或查看项目 docs\desktop.md。
  pause
)
endlocal
