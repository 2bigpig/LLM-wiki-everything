param(
    [ValidateSet('up', 'stop', 'ps', 'logs', 'build')]
    [string]$Action = 'up',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

# Docker 不能直接读取业务 YAML；统一入口解析后只通过子进程环境注入。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先执行 scripts/setup-local.ps1。' }
$env:PYTHONUTF8 = '1'
$composeArgs = @($Action)
if ($Action -eq 'up') { $composeArgs += '-d' }
$composeArgs += $Arguments
& $pythonPath -m wiki_app.configuration compose @composeArgs
if ($LASTEXITCODE -ne 0) { throw "Docker 服务命令失败，退出码 $LASTEXITCODE。" }
