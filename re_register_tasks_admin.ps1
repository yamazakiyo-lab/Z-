$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($root)) {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($root)) {
    $root = (Get-Location).Path
}
$log = Join-Path $root ('re_register_tasks_admin_{0}.txt' -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
Start-Transcript -Path $log -Force
try {
    & (Join-Path $root 'schedule_gdx.ps1') -Time '00:00'
    & (Join-Path $root 'schedule_91other.ps1') -Time '00:00'
    & (Join-Path $root 'schedule_daily_status_check.ps1') -Time '06:00'

    '=== GDX_DailyRun ==='
    schtasks /Query /TN 'GDX_DailyRun' /V /FO LIST | Select-String 'Task To Run|Status'
    '=== OTHER_DailyRun ==='
    schtasks /Query /TN 'OTHER_DailyRun' /V /FO LIST | Select-String 'Task To Run|Status'
    '=== CHECK_DAILYRUNS_0600 ==='
    schtasks /Query /TN 'CHECK_DAILYRUNS_0600' /V /FO LIST | Select-String 'Task To Run|Status'
}
finally {
    Stop-Transcript
}
