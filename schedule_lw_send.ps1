
# schedule_lw_send.ps1
# Register LINE WORKS Bot scheduled tasks
# Tasks:
#   LW_Send_Morning       Daily 10:00  --send
#   LW_Send_Afternoon     Daily 15:00  --send
#   LW_Ranking_Weekly     Daily 10:15  --ranking-weekly (fires only on first workday of week)
#   LW_Cleanup_Reminder   Monthly 1st  10:05  --cleanup-reminder
#   LW_Holiday_Reminder   Yearly May 6 10:10  --holiday-reminder

if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
if (-not $PSScriptRoot) {
    $PSScriptRoot = (Get-Location).Path
}

$pw = $PSScriptRoot

function Resolve-TaskPath {
    param([string]$Path)
    if ($Path -match '^[A-Za-z]:\\') {
        $driveName = $Path.Substring(0,1)
        $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
        if ($drive -and $drive.DisplayRoot) {
            $relative = $Path.Substring(2).TrimStart('\')
            return Join-Path $drive.DisplayRoot $relative
        }
    }
    return $Path
}

$batSend    = Resolve-TaskPath (Join-Path $pw 'run_lw_send_wrapper.bat')
$batCleanup = Resolve-TaskPath (Join-Path $pw 'run_lw_cleanup_wrapper.bat')
$batHoliday = Resolve-TaskPath (Join-Path $pw 'run_lw_holiday_wrapper.bat')
$batRanking = Resolve-TaskPath (Join-Path $pw 'run_lw_ranking_wrapper.bat')

Write-Host "bat: $batSend"

$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable

# LW_Send_Morning: daily 10:00
$a1 = New-ScheduledTaskAction -Execute $batSend
$t1 = New-ScheduledTaskTrigger -Daily -At "10:00"
Register-ScheduledTask -TaskName "LW_Send_Morning" -Action $a1 -Trigger $t1 -Settings $settings -RunLevel Highest -Force | Out-Null
Write-Host "OK: LW_Send_Morning 10:00"

# LW_Send_Afternoon: daily 15:00
$a2 = New-ScheduledTaskAction -Execute $batSend
$t2 = New-ScheduledTaskTrigger -Daily -At "15:00"
Register-ScheduledTask -TaskName "LW_Send_Afternoon" -Action $a2 -Trigger $t2 -Settings $settings -RunLevel Highest -Force | Out-Null
Write-Host "OK: LW_Send_Afternoon 15:00"

# LW_Ranking_Weekly: daily 10:15 (runs only on first workday of week)
$a3 = New-ScheduledTaskAction -Execute $batRanking
$t3 = New-ScheduledTaskTrigger -Daily -At "10:15"
Register-ScheduledTask -TaskName "LW_Ranking_Weekly" -Action $a3 -Trigger $t3 -Settings $settings -RunLevel Highest -Force | Out-Null
Write-Host "OK: LW_Ranking_Weekly daily 10:15 (first workday only)"

# LW_Cleanup_Reminder: every 2 weeks at 14:00
$trCleanup = '"' + $batCleanup + '"'
schtasks /Create /SC WEEKLY /MO 2 /D MON /TN "LW_Cleanup_Reminder" /TR $trCleanup /ST 14:00 /RL HIGHEST /F
Write-Host "OK: LW_Cleanup_Reminder every 2 weeks Monday 14:00"

# LW_Holiday_Reminder: yearly May 6 at 10:10
$trHoliday = '"' + $batHoliday + '"'
schtasks /Create /SC MONTHLY /D 6 /M MAY /TN "LW_Holiday_Reminder" /TR $trHoliday /ST 10:10 /RL HIGHEST /F
Write-Host "OK: LW_Holiday_Reminder yearly May6 10:10"

# CHECK_DAILYRUNS: daily 12:00
$checkLogScript = Join-Path $pw 'check_and_cleanup_logs.ps1'
$trCheckLogs = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' + $checkLogScript + '"'
schtasks /Create /SC DAILY /TN "CHECK_DAILYRUNS" /TR $trCheckLogs /ST 12:00 /RL HIGHEST /F
Write-Host "OK: CHECK_DAILYRUNS 12:00"

Write-Host ""
Write-Host "=== LW Tasks ==="
Get-ScheduledTask | Where-Object { $_.TaskName -like "LW_*" } |
    ForEach-Object {
        $info = Get-ScheduledTaskInfo $_.TaskName -ErrorAction SilentlyContinue
        [PSCustomObject]@{ TaskName = $_.TaskName; NextRun = $info.NextRunTime }
    } | Format-Table -AutoSize
