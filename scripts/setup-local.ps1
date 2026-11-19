param([switch]$SkipFFmpeg)

# 本地后端只安装业务依赖；OCR/ASR/BGE 的重模型依赖留在 Docker。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    # Windows 的默认代码页可能是 GBK，pip 读取含中文的 UTF-8 约束文件时需显式启用 UTF-8。
    $env:PYTHONUTF8 = '1'
    if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败，请检查 Python 3.11–3.13 是否可用。' }
    }
    & ./.venv/Scripts/python.exe -m pip install --cache-dir data/dev/pip-cache -c backend/constraints.txt -e './backend[dev]'
    if ($LASTEXITCODE -ne 0) { throw '本地 Python 依赖安装失败。' }
    & ./.venv/Scripts/python.exe -m wiki_app.configuration migrate-env
    if ($LASTEXITCODE -ne 0) { throw '旧环境迁移失败，原文件已保留。' }
    $publicJson = & ./.venv/Scripts/python.exe -m wiki_app.configuration public
    if ($LASTEXITCODE -ne 0) { throw '请检查 config.yaml 配置。' }
    $public = $publicJson | ConvertFrom-Json
    $ffmpeg = Get-ChildItem -LiteralPath $public.ffmpeg_dir -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $SkipFFmpeg -and -not $ffmpeg -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        # 官方 Windows 便携包解压到项目内；中断时保留 zip，重试可重新下载。
        New-Item -ItemType Directory -Force -Path $public.ffmpeg_dir | Out-Null
        $archive = Join-Path $projectRoot 'data/tools/ffmpeg.zip'
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri $public.ffmpeg_download_url -TimeoutSec $public.ffmpeg_download_timeout_seconds -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $public.ffmpeg_dir -Force
    }
    Write-Host '本地环境已准备；接着运行 start-services.ps1、数据库 migrate 和 start-backend.ps1。'
}
finally { Pop-Location }
