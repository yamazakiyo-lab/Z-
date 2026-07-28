# GDX_DailyRun log check script (for 12:00 execution)
# Encoding: UTF-8

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = Get-Location }
$logDir = Join-Path $scriptDir "logs"

# Get today's date
$today = Get-Date -Format "yyyyMMdd"
$hostname = $env:COMPUTERNAME

# Search for today's logs
$todayLogs = @(Get-ChildItem -Path $logDir -Filter "gdx_run_${today}_*_${hostname}.txt" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)

$checkTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$checkLog = "[$checkTime] Log check started`r`n"

if ($todayLogs.Count -eq 0) {
    $checkLog += "[ERROR] No log found for today (expected: gdx_run_${today}_*_${hostname}.txt)`r`n`r`n"
    $status = "NO_LOG"
    $exitCode = 1
} else {
    $latestLog = $todayLogs[0]
    $checkLog += "[OK] Log detected: $($latestLog.Name) (LastWrite: $($latestLog.LastWriteTime))`r`n`r`n"
    
    # Check last lines of log
    $logContent = Get-Content -Path $latestLog.FullName -Tail 20 -Raw
    $checkLog += "=== LOG TAIL ===" + "`r`n"
    $checkLog += $logContent
    $checkLog += "`r`n`r`n"
    
    # 成功マーカーで判定（check_daily_run_status.ps1 と同じ基準）
    if ($logContent -match "=== 全工程 完了 ===|===== 完了 =====") {
        $status = "OK"
        $exitCode = 0
    } else {
        $status = "ERROR"
        $exitCode = 1
    }
}

$checkLog += "=== RESULT ===" + "`r`n"
$checkLog += "Status: $status`r`n"
$checkLog += "Time: $checkTime`r`n"
$checkLog += "Host: $hostname`r`n"

# Save check log
$checkLogPath = Join-Path $logDir "gdx_check_${today}.log"
$checkLog | Out-File -FilePath $checkLogPath -Encoding UTF8 -Append

Write-Host $checkLog
exit $exitCode
