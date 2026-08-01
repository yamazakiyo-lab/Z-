if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$pw = $PSScriptRoot
$logdir = Join-Path $pw 'logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }
$log = Join-Path $logdir ("lw_morning_run_{0}_{1}.txt" -f $ts, $env:COMPUTERNAME)

Start-Transcript -Path $log -Force
try {
    Write-Host "=== LW_Morning_Greeting $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
    Push-Location $pw
    try {
        py (Join-Path $pw 'lw_annotation_bot.py') --morning-greeting
        Write-Host "Exit code: $LASTEXITCODE"
        # カレンダーのスナップショットをBlobへ(AI Q&Aの予定回答用。失敗しても続行)
        try {
            Write-Host "[CAL] カレンダースナップショット更新..."
            py (Join-Path $pw 'lw_calendar.py') --export
        } catch {}
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "ERROR: $_"
} finally {
    Stop-Transcript
}
