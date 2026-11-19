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
    $ffmpeg = Get-ChildItem -LiteralPath 'data/tools/ffmpeg' -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $SkipFFmpeg -and -not $ffmpeg -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        # 官方 Windows 便携包解压到项目内；中断时保留 zip，重试可重新下载。
        New-Item -ItemType Directory -Force -Path 'data/tools/ffmpeg' | Out-Null
        $archive = Join-Path $projectRoot 'data/tools/ffmpeg.zip'
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath 'data/tools/ffmpeg' -Force
    }
    Write-Host '本地环境已准备；接着启动 Docker 服务并运行 migrate、api、worker。'
}
finally { Pop-Location }
