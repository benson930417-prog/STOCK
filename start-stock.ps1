[CmdletBinding()]
param([switch]$Local, [string]$SshHost = 'stock-arm-primary', [int]$Port = 18501)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'local-entry.ps1')
Enter-LocalWorkspace -SourceRoot $PSScriptRoot -ScriptRelative 'start-stock.ps1' -BoundParameters $PSBoundParameters -ExtraArguments $args
if ($Local) {
    if (-not $env:STOCK_GLOBAL_MARKET_DB -or -not (Test-Path -LiteralPath $env:STOCK_GLOBAL_MARKET_DB -PathType Leaf)) {
        throw 'Local development requires an explicit read-only market DB in STOCK_GLOBAL_MARKET_DB. Normal startup uses the ARM website.'
    }
    & python -m streamlit run (Join-Path $PSScriptRoot 'app.py') --server.address 127.0.0.1 --server.port $Port
    exit $LASTEXITCODE
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid local port' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; no existing process was stopped."
}
$openBrowser = "1..40 | ForEach-Object { try { Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:$Port/' -TimeoutSec 1 | Out-Null; Start-Process 'http://127.0.0.1:$Port/'; break } catch { Start-Sleep -Milliseconds 500 } }"
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $openBrowser | Out-Null
Write-Host "Connecting to the existing ARM dashboard. Close this window to close the tunnel."
& ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N -L "127.0.0.1:${Port}:127.0.0.1:8501" $SshHost
exit $LASTEXITCODE
