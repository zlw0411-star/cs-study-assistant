param([string]$PythonExecutable, [switch]$Desktop)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if ($PythonExecutable) {
    if (-not (Test-Path -LiteralPath $PythonExecutable)) { throw "指定的 Python 不存在：$PythonExecutable" }
    $Python = (Resolve-Path -LiteralPath $PythonExecutable).Path
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($PythonCommand) { $Python = 'py' }
    else {
        $UvPythonRoot = Join-Path $env:APPDATA 'uv\python'
        $FoundPython = Get-ChildItem -Path $UvPythonRoot -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'cpython-3\.12[^\\]*-windows-x86_64-none\\python\.exe$' } |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($FoundPython) { $Python = $FoundPython.FullName }
        else { throw '找不到 Python 3.12。请安装 Python 3.12、uv 管理的 Python，或传入 -PythonExecutable 完整路径。' }
    }
}

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($Python -eq 'py') { & py -3.12 -X utf8 -m venv .venv } else { & $Python -X utf8 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw '创建项目虚拟环境失败。' }
}
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$VenvVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($VenvVersion -ne '3.12') { throw "现有 .venv 使用 Python $VenvVersion；本项目要求 Python 3.12。请手动移走该 .venv 后重新运行 setup.ps1。" }
& $VenvPython -X utf8 -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw '升级 pip 失败，请检查网络后重试。' }
if ($Desktop) {
    & $VenvPython -X utf8 -m pip install -r requirements-desktop.txt
} else {
    & $VenvPython -X utf8 -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。请检查网络后重新运行 scripts/setup.ps1。' }
& $VenvPython -X utf8 -m pip freeze | Set-Content -Encoding utf8 'installed-versions.txt'
if ($Desktop) { Write-Host 'Desktop dependencies installed. Double click 启动桌面版.cmd in the project folder.' }
Write-Host '基础环境已就绪。资料整理使用本地SQLite；在线模型仅在资料分析页面点击分析时调用。运行 scripts/start.ps1 启动通用资料分析助手，运行 scripts/start_408.ps1 启动旧版408真题学习台。'
