param([int]$Port = 8767)
$ErrorActionPreference = 'Stop'
$reviewRecordPath = Join-Path $PSScriptRoot "run\server-$Port.json"
if (-not (Test-Path -LiteralPath $reviewRecordPath)) { throw 'No owned server record found.' }
$reviewRecord = Get-Content -LiteralPath $reviewRecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
$reviewProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$reviewRecord.pid)"
if (-not $reviewProcess -or -not $reviewProcess.CommandLine.Contains($reviewRecord.cli) -or -not $reviewProcess.CommandLine.Contains($reviewRecord.session)) {
    throw 'PID absent or reused by another process; nothing was stopped.'
}
Stop-Process -Id ([int]$reviewRecord.pid)
Write-Output "Stopped server on $Port. Session and saved choices remain."
