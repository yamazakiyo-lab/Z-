param(
    [switch]$DryRun = $false
)

if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }
$hostName = $env:COMPUTERNAME
$pw = $PSScriptRoot
if (-not (Test-Path $pw)) { New-Item -ItemType Directory -Path $pw | Out-Null }
$logdir = Join-Path $pw 'logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }

$log = Join-Path $logdir ("lw_send_run_{0}_{1}.txt" -f $ts, $hostName)

Start-Transcript -Path $log -Force
try {
    Write-Host "=== LW_Send_Annotation $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
    if ($DryRun) {
        Write-Host "[DRY-RUN] ドライランモードで実行します"
    }
    $launcher = 'py'
    $script = Join-Path $pw 'lw_annotation_bot.py'
    Push-Location $pw
    try {
        Write-Host "Working directory: $(Get-Location)"
        Write-Host "Script: $script"
        $args_list = @($script, '--send')
        if ($DryRun) { $args_list += '--dry-run' }
        Write-Host "Executing: $launcher $args_list"
        & $launcher $args_list
        $exitCode = $LASTEXITCODE
        Write-Host "Exit code: $exitCode"

        # タスクステータスを Blob に書く
        if (-not $DryRun) {
            $lwSendStatus = if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }
            & $launcher (Join-Path $pw 'write_task_status.py') --task lw_send --status $lwSendStatus --message "exit=$exitCode"
        }
    } finally {
        Pop-Location
    }
} catch {
    Write-Host "ERROR: $_"
    Write-Host $_.ScriptStackTrace
    if (-not $DryRun) {
        & $launcher (Join-Path $pw 'write_task_status.py') --task lw_send --status FAIL --message "$_"
    }
} finally {
    Stop-Transcript
    # Y: ドライブにもコピー（ラップトップから確認できるよう）
    $yLogDir = 'Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理\lw_send_logs'
    if ((Get-PSDrive Y -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath 'Y:\' -PathType Container -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path -LiteralPath $yLogDir)) {
            New-Item -ItemType Directory -Path $yLogDir -Force | Out-Null
        }
        try { Copy-Item -LiteralPath $log -Destination $yLogDir -Force } catch {}
    }
}
