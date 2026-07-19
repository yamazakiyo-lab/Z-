param(
    [string]$Source      = $PSScriptRoot,
    [string]$Destination = '\\192.168.2.252\本社共有$\管理本部\情報管理課\tseg_vscode\Zフォルダ整理',
    [int]   $DebounceSeconds = 5
)

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

$ErrorActionPreference = 'Stop'

$LogDir = Join-Path $Source 'logs'
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$LogPath = Join-Path $LogDir ('watch_sync_{0}.log' -f (Get-Date -Format 'yyyyMMdd'))
function Write-Log {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

function Invoke-RoboSync {
    $RoboArgs = @(
        $Source
        $Destination
        '/R:3'
        '/W:5'
        '/FFT'
        '/Z'
        '/XD'
        '.git'
        'logs'
        'archive'
        '__pycache__'
        'venv'
        'lw_logs'
        '.ipynb_checkpoints'
        '/XF'
        'credentials.json'
        'token.json'
        'token.revoked.*.json'
        '*.pyc'
        '.DS_Store'
        'Thumbs.db'
        'daily_runs_*.txt'
        're_register_tasks_admin_*.txt'
        'lw_sync_last.log'
        'sync_log.txt'
    )
    & robocopy @RoboArgs | Out-Null
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ge 8) {
        Write-Log "robocopy エラー (exit $ExitCode)"
    } else {
        Write-Log "同期完了 (exit $ExitCode)"
    }
}

# ── FileSystemWatcher セットアップ ──────────────────────────────────────────
$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $Source
$watcher.IncludeSubdirectories = $true
$watcher.NotifyFilter = (
    [System.IO.NotifyFilters]::LastWrite -bor
    [System.IO.NotifyFilters]::FileName  -bor
    [System.IO.NotifyFilters]::DirectoryName
)

# 監視対象外パターン（logs/, lw_logs/, __pycache__/ 配下は無視）
$script:pendingSync    = $false
$script:lastChangeTime = [DateTime]::MinValue
$script:lastOfflineLog = [DateTime]::MinValue

$onEvent = {
    $path = $Event.SourceEventArgs.FullPath
    # logs/ や __pycache__/ 配下、.log / .pyc は無視
    if ($path -match '\\(logs|lw_logs|__pycache__|archive|\.git)\\') { return }
    if ($path -match '\.(log|pyc|tmp)$') { return }
    $script:pendingSync = $true
    $script:lastChangeTime = Get-Date
}

$watcher.EnableRaisingEvents = $true
Register-ObjectEvent $watcher 'Changed' -Action $onEvent | Out-Null
Register-ObjectEvent $watcher 'Created' -Action $onEvent | Out-Null
Register-ObjectEvent $watcher 'Deleted' -Action $onEvent | Out-Null
Register-ObjectEvent $watcher 'Renamed' -Action $onEvent | Out-Null

Write-Log "監視開始: $Source"
Write-Log "同期先  : $Destination"
Write-Log "デバウンス: ${DebounceSeconds}秒"

# ── メインループ ─────────────────────────────────────────────────────────────
while ($true) {
    Start-Sleep -Seconds 1

    if (-not $script:pendingSync) { continue }

    $elapsed = ((Get-Date) - $script:lastChangeTime).TotalSeconds
    if ($elapsed -lt $DebounceSeconds) { continue }

    # ネットワーク接続確認（未接続ならフラグを保持して待機）
    if (-not (Test-Path $Destination)) {
        # 未接続ログは 60 秒に 1 回だけ出す
        $now = Get-Date
        if (($now - $script:lastOfflineLog).TotalSeconds -ge 60) {
            Write-Log "Y: 未接続 — 接続を待機中（変更は保留）"
            $script:lastOfflineLog = $now
        }
        continue   # pendingSync はリセットしない
    }

    $script:pendingSync = $false
    Write-Log "変更検知 → 同期中..."
    Invoke-RoboSync
}
