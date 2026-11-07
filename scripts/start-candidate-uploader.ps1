$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
$logRoot = Join-Path $taskRoot 'data/desktop'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
& (Join-Path $taskRoot '.venv/Scripts/python.exe') -u -m clipper.desktop watch >> (Join-Path $logRoot 'candidate-worker.log') 2>&1
exit $LASTEXITCODE
