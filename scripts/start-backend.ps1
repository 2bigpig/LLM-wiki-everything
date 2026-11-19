param(
    [ValidateSet('both', 'api', 'worker', 'migrate')]
    [string]$Service = 'both',
    [switch]$Reload
)

# 固定工作目录，确保 .env、DATA_DIR、数据库迁移都从同一项目根目录解析。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先运行 scripts/setup-local.ps1 创建本地环境。' }
    if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
    # 便携 FFmpeg 只加入当前进程 PATH，不修改 Windows 全局环境变量。
    $ffmpeg = Get-ChildItem -LiteralPath 'data/tools/ffmpeg' -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ffmpeg) { $env:PATH = $ffmpeg.DirectoryName + [IO.Path]::PathSeparator + $env:PATH }
    $env:PYTHONUTF8 = '1'
    switch ($Service) {
        'both' {
            # 启动器负责健康检查、日志前缀和整组停止；Docker 与前端独立管理。
            $launcherArgs = @('-m', 'wiki_app.launcher')
            if ($Reload) { $launcherArgs += '--reload' }
            & $pythonPath @launcherArgs
        }
        'api' {
            $apiArgs = @('-m', 'uvicorn', 'wiki_app.main:app', '--host', '127.0.0.1', '--port', '8000')
            if ($Reload) { $apiArgs += @('--reload', '--reload-dir', 'backend/src') }
            & $pythonPath @apiArgs
        }
        # 使用可导入模块，避免任务名落到 __main__，便于重启后继续消费持久化队列。
        'worker' { & $pythonPath -c 'from wiki_app.worker import main; main()' }
        'migrate' { & $pythonPath -m wiki_app.migrate }
    }
    if ($LASTEXITCODE -ne 0) { throw "$Service 退出异常，退出码 $LASTEXITCODE，请检查终端或 data/logs。" }
}
finally { Pop-Location }
