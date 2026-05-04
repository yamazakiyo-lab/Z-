$pw = $PSScriptRoot
$logDir = Join-Path $pw 'logs'
$archiveRoot = Join-Path $pw 'archive'
$retentionDir = Join-Path $archiveRoot 'retention'
$cleanupArchiveDir = Join-Path $archiveRoot 'cleanup-logs'

foreach ($dir in @($logDir, $archiveRoot, $retentionDir, $cleanupArchiveDir)) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

$now = Get-Date
$summaryFile = Join-Path $pw ("daily_runs_summary_{0:yyyyMMdd_HHmmss}.txt" -f $now)
$rootLogCutoff = $now.AddDays(-14)
$dailySummaryCutoff = $now.AddDays(-30)
$archiveCutoff = $now.AddDays(-90)

function Write-SummaryLine {
    Param(
        [string]$Message
    )

    Add-Content -LiteralPath $summaryFile -Value $Message
}

function Move-FilesOlderThan {
    Param(
        [string]$SourceDir,
        [string]$Filter,
        [datetime]$Cutoff,
        [string]$DestinationDir,
        [string[]]$ExcludePaths = @()
    )

    if (-not (Test-Path -LiteralPath $SourceDir)) {
        return
    }

    Get-ChildItem -LiteralPath $SourceDir -Filter $Filter -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.LastWriteTime -lt $Cutoff -and
            ($ExcludePaths -notcontains $_.FullName)
        } |
        ForEach-Object {
            $dest = Join-Path $DestinationDir $_.Name
            try {
                Move-Item -LiteralPath $_.FullName -Destination $dest -Force -ErrorAction Stop
                Write-SummaryLine ("MOVED_RETENTION: {0} -> {1}" -f $_.Name, $dest)
            } catch {
                Write-SummaryLine ("FAILED_RETENTION: {0} -> {1} : {2}" -f $_.Name, $dest, $_)
            }
        }
}

function Remove-FilesOlderThan {
    Param(
        [string]$TargetDir,
        [datetime]$Cutoff,
        [switch]$Recurse
    )

    if (-not (Test-Path -LiteralPath $TargetDir)) {
        return
    }

    Get-ChildItem -LiteralPath $TargetDir -File -ErrorAction SilentlyContinue -Recurse:$Recurse |
        Where-Object { $_.LastWriteTime -lt $Cutoff } |
        ForEach-Object {
            try {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                Write-SummaryLine ("DELETED_ARCHIVE: {0}" -f $_.FullName)
            } catch {
                Write-SummaryLine ("FAILED_DELETE: {0} : {1}" -f $_.FullName, $_)
            }
        }
}

function Remove-EmptyDirectories {
    Param(
        [string]$RootDir
    )

    if (-not (Test-Path -LiteralPath $RootDir)) {
        return
    }

    Get-ChildItem -LiteralPath $RootDir -Directory -Recurse -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        ForEach-Object {
            try {
                if (-not (Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue)) {
                    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                    Write-SummaryLine ("REMOVED_EMPTY_DIR: {0}" -f $_.FullName)
                }
            } catch {
            }
        }
}

Write-SummaryLine ("Summary generated: {0}" -f $now)
Write-SummaryLine ""
Write-SummaryLine ("Retention rules: root logs=14d, daily summaries=30d, sync logs=14d, archive/logs=90d")
Write-SummaryLine ""

$patterns = @('gdx_run_*.txt','other_run_*.txt','gdx_production_run_log*.txt','schedule_*_log.txt','re_register_tasks_log*.txt')
$found = @()
foreach ($p in $patterns) {
    $items = Get-ChildItem -LiteralPath $pw -Filter $p -File -ErrorAction SilentlyContinue
    foreach ($it in $items) {
        $found += $it
    }
}

if ($found.Count -eq 0) {
    Write-SummaryLine "No legacy run logs found in workspace root."
} else {
    Write-SummaryLine "Found legacy run logs to archive into logs/:"
    foreach ($f in $found) {
        $dest = Join-Path $logDir $f.Name
        try {
            Move-Item -LiteralPath $f.FullName -Destination $dest -Force -ErrorAction Stop
            Write-SummaryLine ("MOVED_LEGACY: {0} -> {1}" -f $f.Name, $dest)
        } catch {
            Write-SummaryLine ("FAILED_LEGACY_MOVE: {0} -> {1} : {2}" -f $f.Name, $dest, $_)
        }
    }
}

Move-FilesOlderThan -SourceDir $pw -Filter 'photo_video_91_*.log' -Cutoff $rootLogCutoff -DestinationDir $retentionDir
Move-FilesOlderThan -SourceDir $pw -Filter 'photo_video_general_*.log' -Cutoff $rootLogCutoff -DestinationDir $retentionDir
Move-FilesOlderThan -SourceDir $pw -Filter 'attention_general_*.txt' -Cutoff $rootLogCutoff -DestinationDir $retentionDir
Move-FilesOlderThan -SourceDir $logDir -Filter 'sync_*.log' -Cutoff $rootLogCutoff -DestinationDir $retentionDir

Move-FilesOlderThan -SourceDir $pw -Filter 'daily_runs_summary_*.txt' -Cutoff $dailySummaryCutoff -DestinationDir $retentionDir -ExcludePaths @($summaryFile)
Move-FilesOlderThan -SourceDir $pw -Filter 'daily_runs_attention_*.txt' -Cutoff $dailySummaryCutoff -DestinationDir $retentionDir

try {
    $gdx = schtasks /Query /TN "GDX_DailyRun" /V /FO LIST 2>$null
    $other = schtasks /Query /TN "OTHER_DailyRun" /V /FO LIST 2>$null
    Write-SummaryLine ""
    Write-SummaryLine "--- GDX_DailyRun ---"
    Write-SummaryLine $gdx
    Write-SummaryLine ""
    Write-SummaryLine "--- OTHER_DailyRun ---"
    Write-SummaryLine $other
} catch {
    Write-SummaryLine ("Failed to query scheduled tasks: {0}" -f $_)
}

Remove-FilesOlderThan -TargetDir $logDir -Cutoff $archiveCutoff
Remove-FilesOlderThan -TargetDir $retentionDir -Cutoff $archiveCutoff
Remove-FilesOlderThan -TargetDir $cleanupArchiveDir -Cutoff $archiveCutoff -Recurse
Remove-EmptyDirectories -RootDir $cleanupArchiveDir

Write-SummaryLine ""
Write-SummaryLine "Done."
Write-Output "Summary written to: $summaryFile"
