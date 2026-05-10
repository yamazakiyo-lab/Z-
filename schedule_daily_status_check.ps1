Param(
    [string]$Time = '06:00'
)

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

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

function Resolve-TimeValue {
    Param(
        [string]$Value
    )

    if ($Value -notmatch '^(\d{1,2}):(\d{2})$') {
        throw "Time must be in HH:MM format: $Value"
    }

    $hour = [int]$Matches[1]
    $minute = [int]$Matches[2]

    if ($hour -lt 0 -or $hour -gt 23 -or $minute -lt 0 -or $minute -gt 59) {
        throw "Time is out of range: $Value"
    }

    return [pscustomobject]@{
        Hour   = $hour
        Minute = $minute
        Text   = ('{0:D2}:{1:D2}' -f $hour, $minute)
    }
}

$script = Resolve-ScheduledTaskPath -Path (Join-Path $PSScriptRoot 'check_daily_run_status.ps1')
$timeValue = Resolve-TimeValue -Value $Time

Write-Host "Registering scheduled task 'CHECK_DAILYRUNS_0600' to run daily at $($timeValue.Text)"

try {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour $timeValue.Hour -Minute $timeValue.Minute -Second 0)
    Register-ScheduledTask -TaskName 'CHECK_DAILYRUNS_0600' -Action $action -Trigger $trigger -Force | Out-Null
    Write-Host "Scheduled task 'CHECK_DAILYRUNS_0600' registered."
} catch {
    Write-Host "Failed to register with ScheduledTasks cmdlets. Falling back to schtasks. Error: $_"
    $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $script + '"'
    schtasks /Create /SC DAILY /TN 'CHECK_DAILYRUNS_0600' /TR $tr /ST $timeValue.Text /F
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to register CHECK_DAILYRUNS_0600.'
    }
}