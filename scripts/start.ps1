$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) { throw '尚未安装项目环境，请先运行 scripts/setup.ps1。' }
& $VenvPython -X utf8 -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
if ($LASTEXITCODE -ne 0) { throw 'Streamlit 已退出，请检查上方错误信息。' }
