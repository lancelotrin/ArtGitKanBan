$ErrorActionPreference = "Stop"
$botDir = $PSScriptRoot
Set-Location $botDir
$host.UI.RawUI.WindowTitle = "FeishuBot"
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:LLM_API_KEY) {
    $env:LLM_API_KEY = [Environment]::GetEnvironmentVariable("LLM_API_KEY", "User")
}
# 模型固定在脚本里，不读用户环境里旧的 glm-4-flash；限流时 echo_bot.py 会自动退回旧模型
if (-not $env:LLM_MODEL) { $env:LLM_MODEL = "glm-4.5-flash" }
if (-not $env:LLM_BASE_URL) {
    $env:LLM_BASE_URL = [Environment]::GetEnvironmentVariable("LLM_BASE_URL", "User")
    if (-not $env:LLM_BASE_URL) { $env:LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4" }
}
if (-not $env:LLM_VISION_MODEL) { $env:LLM_VISION_MODEL = "glm-4.6v-flash" }
Write-Host "cwd=$botDir"
Write-Host "LLM_API_KEY set=$([bool]$env:LLM_API_KEY) MODEL=$($env:LLM_MODEL) VISION=$($env:LLM_VISION_MODEL)"

# 清掉残留订阅（用 tasklist/wmic 风格过滤，避免整机 Get-CimInstance 卡死）
$profileRoot = $env:USERPROFILE
if (-not $profileRoot) { $profileRoot = "C:\Users\Administrator" }
$lock = Join-Path $profileRoot ".lark-cli\locks\subscribe_cli_a964587c1d781bd4.lock"
Get-Process python,node -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
        if ($cmd -and ($cmd -match "echo_bot\.py" -or $cmd -match "event \+subscribe")) {
            Write-Host "stop pid=$($_.Id)"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}
Start-Sleep -Seconds 1
if (Test-Path $lock) { Remove-Item $lock -Force -ErrorAction SilentlyContinue }

$logPath = Join-Path $botDir "bot.log"
$errPath = Join-Path $botDir "bot.err.log"
$python = (Get-Command python).Source
if ($python -like "*WindowsApps*") {
    $realPython = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
    if (Test-Path $realPython) { $python = $realPython }
}
$script = Join-Path $botDir "echo_bot.py"
Write-Host "starting $python -u $script"
# 不用 Tee 管道：管道断开时 python 会被一起干掉，窗口却还在
$proc = Start-Process -FilePath $python -ArgumentList @("-u", $script) -WorkingDirectory $botDir -RedirectStandardOutput $logPath -RedirectStandardError $errPath -PassThru -NoNewWindow
Write-Host "echo_bot pid=$($proc.Id) log=$logPath"
Write-Host "等待 Connected...（本窗口可关；进程已在后台）"
Start-Sleep -Seconds 8
Get-Content $logPath -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -Last 15
if ($proc.HasExited) {
    Write-Host "ERROR: echo_bot 已退出 exit=$($proc.ExitCode)"
    Get-Content $errPath -Encoding UTF8 -ErrorAction SilentlyContinue
}
