if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$pw = $PSScriptRoot
$logdir = Join-Path $pw 'logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }
$log = Join-Path $logdir ("lw_evening_run_{0}_{1}.txt" -f $ts, $env:COMPUTERNAME)

Start-Transcript -Path $log -Force
try {
    Write-Host "=== LW_Evening_Reminder $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
    Push-Location $pw
    try {
        py (Join-Path $pw 'lw_annotation_bot.py') --evening-reminder
        Write-Host "Exit code: $LASTEXITCODE"
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "ERROR: $_"
} finally {
    Stop-Transcript
}
