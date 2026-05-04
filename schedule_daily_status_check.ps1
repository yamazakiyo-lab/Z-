Param(
    [string]$Time = '06:00'
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

$script = Resolve-ScheduledTaskPath -Path (Join-Path $PSScriptRoot 'check_daily_run_status.ps1')

Write-Host "Registering scheduled task 'CHECK_DAILYRUNS_0600' to run daily at $Time"

try {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $parts = $Time.Split(':')
    $hour = [int]$parts[0]
    $minute = [int]$parts[1]
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour $hour -Minute $minute -Second 0)
    Register-ScheduledTask -TaskName 'CHECK_DAILYRUNS_0600' -Action $action -Trigger $trigger -Force | Out-Null
    Write-Host "Scheduled task 'CHECK_DAILYRUNS_0600' registered."
} catch {
    Write-Host "Failed to register with ScheduledTasks cmdlets. Falling back to schtasks. Error: $_"
    $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $script + '"'
    schtasks /Create /SC DAILY /TN 'CHECK_DAILYRUNS_0600' /TR $tr /ST $Time /F
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to register CHECK_DAILYRUNS_0600.'
    }
}