param(
    [Parameter(Mandatory=$true)][string]$Root,
    [string]$DataDirectory
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) { throw '尚未安装项目环境，请先运行 scripts/setup.ps1。' }
$ResolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$Arguments = @('-X', 'utf8', 'tools/import_exams.py', $ResolvedRoot)
if ($DataDirectory) { $Arguments += @('--data-dir', $DataDirectory) }
& $VenvPython @Arguments
if ($LASTEXITCODE -ne 0) { throw '导入过程有失败项，请检查上方摘要。' }
