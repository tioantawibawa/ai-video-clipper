param([string]$Config = 'data/daily-worker.json', [switch]$Once)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
$taskArguments = @('-m', 'clipper.daily_worker', '--config', $Config)
if ($Once) { $taskArguments += '--once' }
& (Join-Path $taskRoot '.venv/Scripts/python.exe') @taskArguments
exit $LASTEXITCODE
