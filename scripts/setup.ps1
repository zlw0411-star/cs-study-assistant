param([string]$PythonExecutable)
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
& $VenvPython -X utf8 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。请检查网络后重新运行 scripts/setup.ps1。' }
& $VenvPython -X utf8 -m pip freeze | Set-Content -Encoding utf8 'installed-versions.txt'
Write-Host '基础环境已就绪。题目解析与学习记录使用本地SQLite；不需要下载生成模型。'
