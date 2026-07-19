Param(
    [switch]$NoPopup
)

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

$ErrorActionPreference = 'Stop'

$workspace = $PSScriptRoot
$localLogDir = Join-Path $workspace 'logs'
$remoteLogDir = $workspace
$now = Get-Date
$today = $now.Date
$summaryFile = Join-Path $workspace ("daily_runs_summary_{0:yyyyMMdd_HHmmss}.txt" -f $now)
$attentionFile = Join-Path $workspace ("daily_runs_attention_{0:yyyyMMdd_HHmmss}.txt" -f $now)

function Get-TaskInfo {
    Param(
        [string]$TaskName
    )

    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop

        return [pscustomobject]@{
            TaskName = $TaskName
            Exists = $true
            LastRunTime = $info.LastRunTime
            LastResult = $info.LastTaskResult
            NextRunTime = $info.NextRunTime
            Status = [string]$task.State
            Enabled = [bool]$task.Settings.Enabled
            Action = (($task.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join '; ')
        }
    } catch {
        return [pscustomobject]@{
            TaskName = $TaskName
            Exists = $false
            LastRunTime = $null
            LastResult = $null
            NextRunTime = $null
            Status = 'NotFound'
            Enabled = $false
            Action = ''
        }
    }
}

function Get-LatestFileToday {
    Param(
        [string]$Path,
        [string]$Filter
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    Get-ChildItem -LiteralPath $Path -Filter $Filter -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $today } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function Get-LatestFileBefore {
    Param(
        [string]$Path,
        [string]$Filter,
        [datetime]$Before
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    Get-ChildItem -LiteralPath $Path -Filter $Filter -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $Before } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function Test-FileContains {
    Param(
        [string]$Path,
        [string[]]$Patterns
    )

    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $false
    }

    try {
        $match = Select-String -LiteralPath $Path -Pattern $Patterns -SimpleMatch -ErrorAction SilentlyContinue | Select-Object -First 1
        return $null -ne $match
    } catch {
        return $false
    }
}

function Get-RunVerdict {
    Param(
        [pscustomobject]$TaskInfo,
        [System.IO.FileInfo]$TranscriptFile,
        [System.IO.FileInfo]$MarkerFile,
        [bool]$MarkerMatched
    )

    if (-not $TaskInfo.Exists) {
        return 'Not registered'
    }
    if (-not $TaskInfo.Enabled) {
        return 'Disabled'
    }
    if (-not $TaskInfo.LastRunTime -or $TaskInfo.LastRunTime -lt $today) {
        return 'Not run today'
    }
    if ($MarkerMatched) {
        return 'Likely success'
    }
    if ($TranscriptFile) {
        return 'Ran today, verify details'
    }
    if ($MarkerFile) {
        return 'Ran today, verify details'
    }
    return 'Ran today, limited evidence'
}

function Get-LogMetrics {
    Param(
        [System.IO.FileInfo]$LogFile,
        [string]$Kind
    )

    $metrics = [ordered]@{}
    $attention = @()

    if (-not $LogFile -or -not (Test-Path -LiteralPath $LogFile.FullName)) {
        return [pscustomobject]@{
            WarnCount = $null
            Metrics = $metrics
            Attention = $attention
        }
    }

    try {
        $lines = Get-Content -LiteralPath $LogFile.FullName -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            WarnCount = $null
            Metrics = $metrics
            Attention = $attention
        }
    }

    $attention = @(
        $lines |
            Where-Object { $_ -match '\[WARN\]|\[ERROR\]|\bSKIP\b|失敗|スキップ|未対応|未検出' } |
            Select-Object -First 20
    )
    $metrics['warn_count'] = $attention.Count

    foreach ($line in $lines) {
        if ($line -match '^\[[^\]]+\]\[(?:INFO|WARN|ERROR)\] (?<key>[A-Z][A-Z0-9_]+): (?<value>\d+)$') {
            $metrics[$Matches['key']] = [int]$Matches['value']
            continue
        }
        if ($Kind -eq 'GDX' -and $line -match '^\[[^\]]+\]\[INFO\] \[91\] 対象Aフォルダ数: (?<count>\d+)$') {
            $metrics['A_TARGET_COUNT'] = [int]$Matches['count']
            continue
        }
        if ($Kind -eq 'GDX' -and $line -match '^\[[^\]]+\]\[INFO\] \[91:(?<workno>[^\]]+)\] A直下->B4 移動数: (?<count>\d+)$') {
            if (-not $metrics.Contains('A_TO_B4_MOVED')) {
                $metrics['A_TO_B4_MOVED'] = 0
            }
            $metrics['A_TO_B4_MOVED'] += [int]$Matches['count']
            continue
        }
    }

    return [pscustomobject]@{
        WarnCount = $metrics['warn_count']
        Metrics = $metrics
        Attention = $attention
    }
}

function Format-MetricLines {
    Param(
        [hashtable]$Current,
        [hashtable]$Previous,
        [string]$Label
    )

    $keys = @($Current.Keys + $Previous.Keys | Sort-Object -Unique)
    $lines = @("[$Label Metrics]")
    if (-not $keys) {
        return @($lines + 'none')
    }

    foreach ($key in $keys) {
        $cur = if ($Current.Contains($key)) { [int]$Current[$key] } else { $null }
        $prev = if ($Previous.Contains($key)) { [int]$Previous[$key] } else { $null }
        $delta = if ($null -ne $cur -and $null -ne $prev) { $cur - $prev } else { $null }
        $deltaText = if ($null -eq $delta) { 'n/a' } elseif ($delta -ge 0) { "+$delta" } else { "$delta" }
        $lines += ("{0}: current={1}; previous={2}; delta={3}" -f $key, $cur, $prev, $deltaText)
    }
    return $lines
}

function Add-AttentionBlock {
    Param(
        [System.Collections.Generic.List[string]]$Buffer,
        [string]$Title,
        [string[]]$Lines,
        [string]$LogPath
    )

    $Buffer.Add("[$Title]")
    $Buffer.Add("Log: $LogPath")
    if ($Lines -and $Lines.Count -gt 0) {
        foreach ($line in $Lines) {
            $Buffer.Add($line)
        }
    } else {
        $Buffer.Add('none')
    }
    $Buffer.Add('')
}

$gdxTask = Get-TaskInfo -TaskName 'GDX_DailyRun'
$otherTask = Get-TaskInfo -TaskName 'OTHER_DailyRun'

$gdxTranscript = Get-LatestFileToday -Path $localLogDir -Filter 'gdx_run_*.txt'
$otherTranscript = Get-LatestFileToday -Path $localLogDir -Filter 'other_run_*.txt'
$gdxMarker = Get-LatestFileToday -Path $remoteLogDir -Filter 'photo_video_91_*.log'
$otherMarker = Get-LatestFileToday -Path $remoteLogDir -Filter 'photo_video_general_*.log'

$prevGdxMarker = Get-LatestFileBefore -Path $remoteLogDir -Filter 'photo_video_91_*.log' -Before $today
$prevOtherMarker = Get-LatestFileBefore -Path $remoteLogDir -Filter 'photo_video_general_*.log' -Before $today

$gdxMarkerMatched = $false
if ($gdxMarker) {
    $gdxMarkerMatched = Test-FileContains -Path $gdxMarker.FullName -Patterns @('=== 全工程 完了 ===', '===== 完了 =====')
}

$gdxMetrics = Get-LogMetrics -LogFile $gdxMarker -Kind 'GDX'
$prevGdxMetrics = Get-LogMetrics -LogFile $prevGdxMarker -Kind 'GDX'
$otherMetrics = Get-LogMetrics -LogFile $otherMarker -Kind 'OTHER'
$prevOtherMetrics = Get-LogMetrics -LogFile $prevOtherMarker -Kind 'OTHER'

$gdxVerdict = Get-RunVerdict -TaskInfo $gdxTask -TranscriptFile $gdxTranscript -MarkerFile $gdxMarker -MarkerMatched $gdxMarkerMatched
$otherVerdict = Get-RunVerdict -TaskInfo $otherTask -TranscriptFile $otherTranscript -MarkerFile $otherMarker -MarkerMatched ($null -ne $otherMarker)

$attentionLines = New-Object 'System.Collections.Generic.List[string]'
$attentionLines.Add("Attention generated: $now")
$attentionLines.Add('')
Add-AttentionBlock -Buffer $attentionLines -Title 'GDX Attention' -Lines $gdxMetrics.Attention -LogPath $gdxMarker.FullName
Add-AttentionBlock -Buffer $attentionLines -Title 'OTHER Attention' -Lines $otherMetrics.Attention -LogPath $otherMarker.FullName
Set-Content -LiteralPath $attentionFile -Value $attentionLines -Encoding UTF8

$lines = @(
    "Summary generated: $now",
    '',
    '[GDX_DailyRun]',
    "Verdict: $gdxVerdict",
    "Last run: $($gdxTask.LastRunTime)",
    "Last result: $($gdxTask.LastResult)",
    "Next run: $($gdxTask.NextRunTime)",
    "State: $($gdxTask.Status)",
    "Enabled: $($gdxTask.Enabled)",
    "Action: $($gdxTask.Action)",
    "Local log: $($gdxTranscript.FullName)",
    "Marker log: $($gdxMarker.FullName)",
    "Marker matched: $gdxMarkerMatched",
    "Previous marker log: $($prevGdxMarker.FullName)",
    '',
    '[OTHER_DailyRun]',
    "Verdict: $otherVerdict",
    "Last run: $($otherTask.LastRunTime)",
    "Last result: $($otherTask.LastResult)",
    "Next run: $($otherTask.NextRunTime)",
    "State: $($otherTask.Status)",
    "Enabled: $($otherTask.Enabled)",
    "Action: $($otherTask.Action)",
    "Local log: $($otherTranscript.FullName)",
    "Marker log: $($otherMarker.FullName)",
    "Previous marker log: $($prevOtherMarker.FullName)",
    '',
    "Attention file: $attentionFile",
    '',
    'Done.'
)

$lines += ''
$lines += Format-MetricLines -Current $gdxMetrics.Metrics -Previous $prevGdxMetrics.Metrics -Label 'GDX'
$lines += ''
$lines += Format-MetricLines -Current $otherMetrics.Metrics -Previous $prevOtherMetrics.Metrics -Label 'OTHER'

Set-Content -LiteralPath $summaryFile -Value $lines -Encoding UTF8

$popupMessage = @(
    'Daily run check',
    '',
    "GDX: $gdxVerdict",
    "OTHER: $otherVerdict",
    '',
    "Details: $summaryFile"
) -join [Environment]::NewLine

if (-not $NoPopup) {
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($popupMessage, 'Daily Run Status') | Out-Null
    } catch {
        try {
            $wshell = New-Object -ComObject WScript.Shell
            $null = $wshell.Popup($popupMessage, 30, 'Daily Run Status', 64)
        } catch {
        }
    }
}

Write-Output "Summary written to: $summaryFile"
Write-Output $popupMessage

# ── GDX トランスクリプトログ詳細確認（check_gdx_log.ps1 統合） ────────────────
$gdxCheckScript = Join-Path $workspace 'check_gdx_log.ps1'
if (Test-Path -LiteralPath $gdxCheckScript) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $gdxCheckScript -NoPopup
}