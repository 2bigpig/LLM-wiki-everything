# 本机准备密钥并安全导入旧模型配置；不启动或停止任何服务。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先运行 scripts/setup-local.ps1。' }
    & $pythonPath -m wiki_app.modules.model_management.prepare
    if ($LASTEXITCODE -ne 0) { throw '模型准备失败，请检查上述原因；私密配置已保留。' }
} finally { Pop-Location }
