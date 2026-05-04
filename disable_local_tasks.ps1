$ErrorActionPreference = 'Stop'

$tasks = 'GDX_DailyRun', 'OTHER_DailyRun', 'CHECK_DAILYRUNS_0600'

foreach ($taskName in $tasks) {
    try {
        Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
        Write-Host "Disabled: $taskName"
    } catch {
        Write-Host "Skip/Failed: $taskName ($_ )"
    }
}