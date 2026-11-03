param([Parameter(Mandatory = $true)] [string] $ClientFile)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$clientPath = (Resolve-Path -LiteralPath $ClientFile).Path
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
& (Join-Path $taskRoot '.venv/Scripts/python.exe') -m clipper.youtube_auth --client-file $clientPath
exit $LASTEXITCODE
