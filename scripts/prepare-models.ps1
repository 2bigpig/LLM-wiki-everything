param(
    [switch]$IncludeEmbeddings,
    [switch]$SkipBuild
)

# 下载和加载均在 Docker 内完成；模型保留在 Compose 命名卷中。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw '请先启动 Docker Desktop。Windows GPU 容器使用已有 WSL2 后端。' }
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先执行 scripts/setup-local.ps1。' }
    $env:PYTHONUTF8 = '1'
    if (-not $SkipBuild) {
        & $pythonPath -m wiki_app.configuration compose build ocr asr
        if ($LASTEXITCODE -ne 0) { throw '推理镜像构建失败。' }
    }
    foreach ($kind in @('ocr', 'asr')) {
        & $pythonPath -m wiki_app.configuration compose run --rm --no-deps $kind python -m wiki_app.model_cache $kind
        if ($LASTEXITCODE -ne 0) { throw "$kind 模型下载或加载失败，已下载的缓存会保留。" }
    }
    if ($IncludeEmbeddings) {
        if (-not $SkipBuild) {
            & $pythonPath -m wiki_app.configuration compose build bge
            if ($LASTEXITCODE -ne 0) { throw '向量模型镜像构建失败。' }
        }
        & $pythonPath -m wiki_app.configuration compose run --rm --no-deps bge python embedding_server.py --prepare
        if ($LASTEXITCODE -ne 0) { throw '向量模型下载或加载失败，已下载的缓存会保留。' }
    }
    Write-Host '模型已预下载并加载验证；容器退出后命名卷中的缓存继续保留。'
}
finally {
    Pop-Location
}
