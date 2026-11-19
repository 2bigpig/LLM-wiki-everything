param(
    [ValidateSet('both', 'api', 'worker', 'migrate')]
    [string]$Service = 'both',
    [switch]$Reload
)

# 固定工作目录，配置加载器另行以 YAML 所在目录解析资料路径。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先运行 scripts/setup-local.ps1 创建本地环境。' }
    if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
    $env:PYTHONUTF8 = '1'
    # 只输出准备脚本需要的公共字段，密钥不会通过 JSON 传递。
    $publicJson = & $pythonPath -m wiki_app.configuration public
    if ($LASTEXITCODE -ne 0) { throw '配置校验失败，请检查 config.yaml 或迁移旧 .env。' }
    $public = $publicJson | ConvertFrom-Json
    $ffmpeg = Get-ChildItem -LiteralPath $public.ffmpeg_dir -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ffmpeg) { $env:PATH = $ffmpeg.DirectoryName + [IO.Path]::PathSeparator + $env:PATH }
    switch ($Service) {
        'both' {
            # 启动器负责健康检查、日志前缀和整组停止；Docker 与前端独立管理。
            $launcherArgs = @('-m', 'wiki_app.launcher')
            if ($Reload) { $launcherArgs += '--reload' }
            & $pythonPath @launcherArgs
        }
        'api' {
            # 与统一启动共用 API 参数，独立调试也读取同一端口和工作目录。
            $reloadValue = if ($Reload) { 'True' } else { 'False' }
            & $pythonPath -c "from wiki_app.launcher import api_main; api_main(reload=$reloadValue)"
        }
        # 使用可导入模块，避免任务名落到 __main__，便于重启后继续消费持久化队列。
        'worker' { & $pythonPath -c 'from wiki_app.worker import main; main()' }
        'migrate' { & $pythonPath -m wiki_app.migrate }
    }
    if ($LASTEXITCODE -ne 0) { throw "$Service 退出异常，退出码 $LASTEXITCODE，请检查终端或 data/logs。" }
}
finally { Pop-Location }
