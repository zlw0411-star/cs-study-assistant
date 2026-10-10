$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) { throw '请先运行 scripts/setup.ps1 创建项目环境。' }
$ModelCache = Join-Path $ProjectRoot '.tools\huggingface'
New-Item -ItemType Directory -Force $ModelCache | Out-Null
$env:HF_HOME = $ModelCache
$env:HUGGINGFACE_HUB_CACHE = Join-Path $ModelCache 'hub'

# Install the CPU-only PyTorch wheel into this project's venv, then the BGE stack.
& $VenvPython -X utf8 -m pip install torch --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'CPU 版 PyTorch 下载/安装失败。请检查网络后重试；基础 BM25 仍可使用。' }
& $VenvPython -X utf8 -m pip install -r requirements-models.txt
if ($LASTEXITCODE -ne 0) { throw '模型依赖安装失败。请检查网络、磁盘空间后重试；基础 BM25 不受影响。' }

# Materialize and exercise the real encoder now so download/network failures are visible.
& $VenvPython -X utf8 -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu', revision='7999e1d3359715c523056ef9478215996d62a620'); v=m.encode(['为这个句子生成表示以用于检索相关文章：进程调度'], normalize_embeddings=True); print('BGE CPU 可用，向量维度:', v.shape[1])"
if ($LASTEXITCODE -ne 0) { throw 'BGE 下载或 CPU 编码失败。请检查 Hugging Face 网络/缓存；程序不会用假向量替代。' }

# Keep Ollama binaries and its model cache inside this project.
$OllamaDir = Join-Path $ProjectRoot '.tools\ollama'
$OllamaExe = Join-Path $OllamaDir 'ollama.exe'
$LocalOllama = Get-ChildItem -Path $OllamaDir -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($LocalOllama) { $OllamaExe = $LocalOllama.FullName }
if (-not (Test-Path -LiteralPath $OllamaExe)) {
    $SystemOllama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($SystemOllama) { $OllamaExe = $SystemOllama.Source }
}
if (-not (Test-Path -LiteralPath $OllamaExe)) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $OllamaDir) | Out-Null
    $ZipPath = Join-Path $ProjectRoot '.tools\ollama-windows-amd64.zip'
    $Url = 'https://github.com/ollama/ollama/releases/download/v0.40.2/ollama-windows-amd64.zip'
    Write-Host '正在下载 Ollama Windows GPU 包（约 1.5 GB），文件保存在项目 .tools 目录。'
    try { Invoke-WebRequest -Uri $Url -OutFile $ZipPath }
    catch { throw "Ollama 下载失败：$($_.Exception.Message)。请稍后重试，或先手动安装 Ollama 后重新运行此脚本。" }
    $ActualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
    if ($ActualHash -ne 'e29ad1d5063dd4b54b2492d9b00adab2cff9621bfa654b6c92aa8d6f1fdfe7fc') { throw "Ollama 压缩包 SHA-256 校验失败：$ActualHash。为保护项目环境，已停止解压。" }
    New-Item -ItemType Directory -Force $OllamaDir | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $OllamaDir -Force
    $LocalOllama = Get-ChildItem -Path $OllamaDir -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($LocalOllama) { $OllamaExe = $LocalOllama.FullName }
    if (Test-Path -LiteralPath $OllamaExe) { Remove-Item -LiteralPath $ZipPath -Force }
}
if (-not (Test-Path -LiteralPath $OllamaExe)) { throw "Ollama 压缩包中没有找到 ollama.exe：$OllamaDir" }
$ModelsDir = Join-Path $ProjectRoot 'data\ollama-models'
New-Item -ItemType Directory -Force $ModelsDir | Out-Null
$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_HOST = '127.0.0.1:11434'
$Existing = $false
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null; $Existing = $true } catch {}
if (-not $Existing) {
$Running = Get-CimInstance Win32_Process -Filter "Name='ollama.exe'" -ErrorAction SilentlyContinue
    if (-not $Running) { Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden }
    $Ready = $false
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null; $Ready = $true; break } catch {}
    }
    if (-not $Ready) { throw 'Ollama 服务没有在 60 秒内启动。可检查任务管理器和本地端口 11434。' }
}
$PullExitCode = 0
& $OllamaExe show qwen3:4b *> $null
if ($LASTEXITCODE -ne 0) {
    & $OllamaExe pull qwen3:4b
    $PullExitCode = $LASTEXITCODE
}
if ($PullExitCode -ne 0) {
    Write-Warning 'Ollama CLI 拉取失败（可能是网络重定向限制）；scripts/setup_models.ps1 将用官方 Registry blob 导入并校验 SHA-256。'
    $Registry = 'https://registry.ollama.ai/v2/library/qwen3'
    $ManifestFile = Join-Path $ModelsDir 'qwen3-4b-manifest.json'
    & curl.exe -fL --retry 2 --output $ManifestFile "$Registry/manifests/4b"
    if ($LASTEXITCODE -ne 0) { throw '无法从官方 Ollama Registry 获取 qwen3:4b manifest。' }
    $Manifest = Get-Content -Raw -LiteralPath $ManifestFile | ConvertFrom-Json
    $Layers = @{}
    foreach ($Layer in $Manifest.layers) { $Layers[$Layer.mediaType] = $Layer }
    $Expected = @{
        'application/vnd.ollama.image.model' = 'sha256:3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f'
        'application/vnd.ollama.image.template' = 'sha256:2d54db2b9bb29ce7db54fea63a891f5859603813c555b1f88b5e0994652897f9'
        'application/vnd.ollama.image.params' = 'sha256:cff3f395ef3756ab63e58b0ad1b32bb6f802905cae1472e6a12034e4246fbbdb'
    }
    foreach ($Type in $Expected.Keys) {
        if (-not $Layers.ContainsKey($Type) -or $Layers[$Type].digest -ne $Expected[$Type]) {
            throw "官方 qwen3:4b manifest digest 已变化或不匹配（$Type）；停止导入，避免装入未经核对的模型。"
        }
    }
    if ($Layers['application/vnd.ollama.image.model'].size -ne 2497280480) { throw '官方 qwen3:4b 模型层大小不匹配，停止导入。' }
    $Gguf = Join-Path $ModelsDir 'qwen3-4b.gguf'
    $GgufHash = if (Test-Path -LiteralPath $Gguf) { 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $Gguf).Hash.ToLowerInvariant() } else { '' }
    if ($GgufHash -ne $Expected['application/vnd.ollama.image.model']) {
        & curl.exe -fL --retry 2 --output $Gguf "$Registry/blobs/$($Expected['application/vnd.ollama.image.model'])"
        if ($LASTEXITCODE -ne 0) { throw '无法从官方 Ollama Registry 下载 qwen3:4b 模型层。' }
        $GgufHash = 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $Gguf).Hash.ToLowerInvariant()
        if ($GgufHash -ne $Expected['application/vnd.ollama.image.model']) { throw "qwen3:4b 模型层 SHA-256 不匹配：$GgufHash。文件已保留供排查，不会导入。" }
    } else { Write-Host '复用并再次校验已下载的官方 qwen3:4b 模型层。' }
    $TemplatePath = Join-Path $ModelsDir 'qwen3-4b-template.txt'
    $ParamsPath = Join-Path $ModelsDir 'qwen3-4b-params.json'
    & curl.exe -fL --retry 2 --output $TemplatePath "$Registry/blobs/$($Expected['application/vnd.ollama.image.template'])"
    if ($LASTEXITCODE -ne 0) { throw '无法下载官方 qwen3:4b 模板。' }
    & curl.exe -fL --retry 2 --output $ParamsPath "$Registry/blobs/$($Expected['application/vnd.ollama.image.params'])"
    if ($LASTEXITCODE -ne 0) { throw '无法下载官方 qwen3:4b 参数。' }
    $TemplateHash = 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $TemplatePath).Hash.ToLowerInvariant()
    $ParamsHash = 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $ParamsPath).Hash.ToLowerInvariant()
    if ($TemplateHash -ne $Expected['application/vnd.ollama.image.template']) { throw "qwen3:4b 官方模板 SHA-256 不匹配：$TemplateHash。" }
    if ($ParamsHash -ne $Expected['application/vnd.ollama.image.params']) { throw "qwen3:4b 官方参数 SHA-256 不匹配：$ParamsHash。" }
    $Template = Get-Content -Raw -LiteralPath $TemplatePath
    $Modelfile = @"
FROM ./qwen3-4b.gguf
TEMPLATE """
$Template
"""
PARAMETER repeat_penalty 1
PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
PARAMETER temperature 0.6
PARAMETER top_k 20
PARAMETER top_p 0.95
"@
    $ModelfilePath = Join-Path $ModelsDir 'qwen3-4b.Modelfile'
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ModelfilePath, $Modelfile, $Utf8NoBom)
    Push-Location $ModelsDir
    try { & $OllamaExe create qwen3:4b -f qwen3-4b.Modelfile }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw '从经 SHA-256 校验的官方 qwen3:4b GGUF 导入失败。' }
    Remove-Item -LiteralPath $Gguf, $TemplatePath, $ParamsPath, $ManifestFile, $ModelfilePath -Force
    Write-Host '已用官方Registry中与 qwen3:4b tag 相同的模型层 digest 创建本地 qwen3:4b。'
}
& $VenvPython -X utf8 -m pip freeze | Set-Content -Encoding utf8 'installed-versions.txt'
Write-Host "BGE CPU 模型和 Ollama qwen3:4b 已就绪。Ollama 模型缓存位于 $ModelsDir。"
