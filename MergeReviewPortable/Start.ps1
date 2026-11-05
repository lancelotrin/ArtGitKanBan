param(
    [string]$RepoPath,
    [string]$SessionPath,
    [string]$ReplayBase,
    [int]$Port = 8767
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Choose -Port or stop the owned instance."
}
if ([bool]$RepoPath -eq [bool]$SessionPath) { throw 'Specify exactly one of -RepoPath or -SessionPath.' }
$reviewPython = python -X utf8 -c 'import sys; print(sys.executable)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
$reviewCli = Join-Path $PSScriptRoot 'merge_review.py'
if ($RepoPath) {
    $captureArgs = @('-X', 'utf8', $reviewCli, 'capture', '--repo', $RepoPath, '--out', (Join-Path $PSScriptRoot 'sessions'), '--json')
    if ($ReplayBase) { $captureArgs += @('--replay-base', $ReplayBase) }
    $captureResult = & $reviewPython @captureArgs
    if ($LASTEXITCODE -ne 0) { throw 'Capture failed. Repository was not modified.' }
    $SessionPath = ($captureResult | ConvertFrom-Json).session
}
$reviewSession = (Resolve-Path -LiteralPath $SessionPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $reviewSession 'snapshot.json'))) { throw 'Session has no snapshot.json.' }
$reviewRun = Join-Path $PSScriptRoot 'run'
New-Item -ItemType Directory -Force -Path $reviewRun | Out-Null
$reviewArguments = @('-X', 'utf8', '-u', ('"' + $reviewCli + '"'), 'serve', '--session', ('"' + $reviewSession + '"'), '--host', '0.0.0.0', '--port', "$Port")
$reviewProcess = Start-Process -FilePath $reviewPython -ArgumentList $reviewArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$reviewSession\server.log" -RedirectStandardError "$reviewSession\server-error.log" -PassThru
@{ pid = $reviewProcess.Id; cli = $reviewCli; session = $reviewSession; port = $Port } | ConvertTo-Json | Set-Content -LiteralPath "$reviewRun\server-$Port.json" -Encoding UTF8
Write-Output "Session: $reviewSession"
Write-Output "Local: http://127.0.0.1:$Port"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { Write-Output "LAN: http://$($_.IPAddress):$Port" }
