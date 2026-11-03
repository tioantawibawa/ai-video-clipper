param(
    [Parameter(ValueFromRemainingArguments = $true)] [string[]] $ClipperArgs
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONPATH = Join-Path $taskRoot 'src'
& (Join-Path $taskRoot '.venv/Scripts/python.exe') -m clipper.desktop @ClipperArgs
exit $LASTEXITCODE
