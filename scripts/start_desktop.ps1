$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$Pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Pythonw) -or -not (Test-Path -LiteralPath $Python)) {
    throw '未找到项目桌面运行环境。请先在项目目录运行 scripts\setup.ps1 -Desktop。'
}

$QtCheck = & $Python -c "from PySide6.QtWidgets import QApplication" 2>&1
if ($LASTEXITCODE -ne 0) {
    $Cause = ($QtCheck | Select-Object -First 1 | Out-String).Trim()
    throw "桌面版依赖或 Qt 运行组件无法加载。请运行 scripts\setup.ps1 -Desktop 后重试。诊断：$Cause"
}

Start-Process -FilePath $Pythonw -ArgumentList @('-X', 'utf8', 'desktop_app.py') -WorkingDirectory $ProjectRoot -WindowStyle Hidden
