# DailyRun ログ確認スクリプト (CHECK_DAILYRUNS: 12:00 実行)
# 複数ステップ（GDX, OTHER, AzCopy）の実行結果を検証
# Encoding: UTF-8

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = Get-Location }
$logDir = Join-Path $scriptDir "logs"

# Get today's date
$today = Get-Date -Format "yyyyMMdd"
$hostname = $env:COMPUTERNAME

# Search for today's logs (新ログ形式)
$todayLogs = @(Get-ChildItem -Path $logDir -Filter "dailyrun_${today}_*_${hostname}.txt" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)

$checkTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$checkLog = "[$checkTime] Daily Run Check Started`r`n"

if ($todayLogs.Count -eq 0) {
    $checkLog += "[ERROR] No log found for today (expected: dailyrun_${today}_*_${hostname}.txt)`r`n`r`n"
    $overallStatus = "NO_LOG"
    $exitCode = 1
} else {
    $latestLog = $todayLogs[0]
    $checkLog += "[OK] Log detected: $($latestLog.Name) (LastWrite: $($latestLog.LastWriteTime))`r`n`r`n"
    
    # Check last lines of log (PowerShell 5.1: -Raw と -Tail は同時使用不可)
    $logContent = Get-Content -Path $latestLog.FullName -Raw
    $checkLog += "=== LOG TAIL ===" + "`r`n"
    $logContent | Select-String '.' -Context 0 | Select-Object -Last 30 | ForEach-Object { $checkLog += $_.Line + "`r`n" }
    $checkLog += "`r`n"
    
    # [RESULT] ラインから結果を抽出
    $resultLine = $logContent | Select-String '\[RESULT\]' | Select-Object -Last 1
    
    if ($resultLine) {
        $checkLog += "=== RESULT ANALYSIS ===" + "`r`n"
        $checkLog += $resultLine.Line + "`r`n`r`n"
        
        # 各ステップの結果を検査
        $gdxStatus = 'UNKNOWN'
        $otherStatus = 'UNKNOWN'
        $azcopyStatus = 'UNKNOWN'
        
        if ($resultLine.Line -match 'GDX=(\S+)') { $gdxStatus = $Matches[1] }
        if ($resultLine.Line -match 'OTHER=(\S+)') { $otherStatus = $Matches[1] }
        if ($resultLine.Line -match 'AzCopy=(\S+)') { $azcopyStatus = $Matches[1] }
        
        $checkLog += "GDX   : $gdxStatus`r`n"
        $checkLog += "OTHER : $otherStatus`r`n"
        $checkLog += "AzCopy: $azcopyStatus`r`n`r`n"
        
        # 失敗したステップをアラート
        $failedSteps = @()
        if ($gdxStatus -eq 'FAIL') { $failedSteps += 'GDX' }
        if ($otherStatus -eq 'FAIL') { $failedSteps += 'OTHER' }
        if ($azcopyStatus -eq 'FAIL') { $failedSteps += 'AzCopy' }
        
        if ($failedSteps.Count -gt 0) {
            $checkLog += "[ALERT] 失敗したステップ: " + ($failedSteps -join ', ') + "`r`n"
            $overallStatus = 'FAILED'
            $exitCode = 1
        } else {
            # すべてが PASS または SKIP
            $skippedSteps = @()
            if ($gdxStatus -eq 'SKIP') { $skippedSteps += 'GDX' }
            if ($otherStatus -eq 'SKIP') { $skippedSteps += 'OTHER' }
            if ($azcopyStatus -eq 'SKIP') { $skippedSteps += 'AzCopy' }
            
            if ($skippedSteps.Count -gt 0) {
                $checkLog += "[INFO] スキップされたステップ: " + ($skippedSteps -join ', ') + "`r`n"
            }
            
            $overallStatus = 'OK'
            $exitCode = 0
        }
    } else {
        $checkLog += "[ERROR] [RESULT] line not found in log`r`n`r`n"
        $overallStatus = 'INCOMPLETE'
        $exitCode = 1
    }
}

$checkLog += "`r`n=== CHECK RESULT ===" + "`r`n"
$checkLog += "Overall Status: $overallStatus`r`n"
$checkLog += "Time: $checkTime`r`n"
$checkLog += "Host: $hostname`r`n"

# Save check log
$checkLogPath = Join-Path $logDir "dailyrun_check_${today}.log"
$checkLog | Out-File -FilePath $checkLogPath -Encoding UTF8 -Append

Write-Host $checkLog
exit $exitCode
