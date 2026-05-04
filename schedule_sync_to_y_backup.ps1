Param(
    [string]$Time = '12:00',
    [int]$WeeksInterval = 2
)

function Resolve-ScheduledTaskPath {
    Param(
        [string]$Path
    )

    if ($Path -match '^[A-Za-z]:\\') {
        $driveName = $Path.Substring(0, 1)
        $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
        if ($drive -and $drive.DisplayRoot) {
            $relative = $Path.Substring(2).TrimStart('\\')
            return (Join-Path $drive.DisplayRoot $relative)
        }
    }

    return $Path
}

$script = Resolve-ScheduledTaskPath -Path (Join-Path $PSScriptRoot 'sync_to_y_backup.ps1')

Write-Host "Registering scheduled task 'SYNC_TO_Y_BACKUP' to run every $WeeksInterval weeks at $Time"

try {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $parts = $Time.Split(':')
    $hour = [int]$parts[0]
    $minute = [int]$parts[1]
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval $WeeksInterval -DaysOfWeek Saturday -At (Get-Date -Hour $hour -Minute $minute -Second 0)
    Register-ScheduledTask -TaskName 'SYNC_TO_Y_BACKUP' -Action $action -Trigger $trigger -RunLevel Highest -Force | Out-Null
    Write-Host "Scheduled task 'SYNC_TO_Y_BACKUP' registered."
} catch {
    Write-Host "Failed to register with ScheduledTasks cmdlets. Falling back to schtasks. Error: $_"
    $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $script + '"'
    schtasks /Create /SC WEEKLY /MO $WeeksInterval /D SAT /TN 'SYNC_TO_Y_BACKUP' /TR $tr /ST $Time /RL HIGHEST /F
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to register SYNC_TO_Y_BACKUP.'
    }
    Write-Host "Fallback registration attempted."
}