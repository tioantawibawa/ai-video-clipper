param([string]$Config = 'data/local-worker.json', [switch]$Once)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskArguments = @('-m', 'clipper.local_worker', '--config', $Config)
if ($Once) { $taskArguments += '--once' }
& $taskPython @taskArguments
exit $LASTEXITCODE
