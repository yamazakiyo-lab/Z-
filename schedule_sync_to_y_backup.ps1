Param(
    [string]$Time = '12:00',
    [int]$WeeksInterval = 2
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

$script = Resolve-ScheduledTaskPath -Path (Join-Path $PSScriptRoot 'sync_to_y_backup.ps1')

if ($WeeksInterval -lt 1) {
    throw "WeeksInterval must be 1 or greater: $WeeksInterval"
}

$timeValue = Resolve-TimeValue -Value $Time

$existingTask = $null
try {
    $existingTask = schtasks /Query /TN 'SYNC_TO_Y_BACKUP' /FO LIST /V 2>$null | Out-String
} catch {
    $existingTask = $null
}

if ($existingTask -and $existingTask -match [regex]::Escape($script) -and $existingTask -match 'Status:\s+Ready' -and $existingTask -match 'Days:\s+SAT' -and $existingTask -match 'Months:\s+Every 2 week\(s\)' -and $existingTask -match ('Start Time:\s+' + [regex]::Escape($timeValue.Text + ':00'))) {
    Write-Host "Scheduled task 'SYNC_TO_Y_BACKUP' is already registered with the expected settings."
    return
}

Write-Host "Registering scheduled task 'SYNC_TO_Y_BACKUP' to run every $WeeksInterval weeks at $($timeValue.Text)"

try {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval $WeeksInterval -DaysOfWeek Saturday -At (Get-Date -Hour $timeValue.Hour -Minute $timeValue.Minute -Second 0)
    Register-ScheduledTask -TaskName 'SYNC_TO_Y_BACKUP' -Action $action -Trigger $trigger -RunLevel Highest -Force -ErrorAction Stop | Out-Null
    Write-Host "Scheduled task 'SYNC_TO_Y_BACKUP' registered by ScheduledTasks cmdlets."
} catch {
    Write-Host "Failed to register with ScheduledTasks cmdlets. Falling back to schtasks. Error: $_"
    $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $script + '"'
    schtasks /Create /SC WEEKLY /MO $WeeksInterval /D SAT /TN 'SYNC_TO_Y_BACKUP' /TR $tr /ST $timeValue.Text /RL HIGHEST /F
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to register SYNC_TO_Y_BACKUP.'
    }
    Write-Host "Scheduled task 'SYNC_TO_Y_BACKUP' registered by schtasks fallback."
}