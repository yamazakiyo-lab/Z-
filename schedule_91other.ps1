Param(
    [string]$Time = "00:00"
)

$pw = $PSScriptRoot

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

$script = Resolve-ScheduledTaskPath -Path (Join-Path $pw "run_91other_wrapper.bat")

Write-Host "Registering scheduled task 'OTHER_DailyRun' to run daily at $Time"

try {
    $action = New-ScheduledTaskAction -Execute $script
    $parts = $Time.Split(':')
    $hour = [int]$parts[0]
    $minute = [int]$parts[1]
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour $hour -Minute $minute -Second 0)
    Register-ScheduledTask -TaskName "OTHER_DailyRun" -Action $action -Trigger $trigger -RunLevel Highest -Force
    Write-Host "Scheduled task 'OTHER_DailyRun' registered. To remove: schtasks /Delete /TN \"OTHER_DailyRun\" /F"
} catch {
    Write-Host "Failed to register scheduled task using ScheduledTasks cmdlets. Falling back to schtasks with careful quoting. Error: $_"
    $tr = '"' + $script + '"'
    schtasks /Create /SC DAILY /TN "OTHER_DailyRun" /TR $tr /ST $Time /RL HIGHEST /F
    Write-Host "Fallback registration attempted. To remove: schtasks /Delete /TN \"OTHER_DailyRun\" /F"
}
