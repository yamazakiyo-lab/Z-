
<#
.SYNOPSIS
  学習協力Bot 送信タスクをタスクスケジューラに登録する。

  登録タスク:
    LW_Send_Morning        毎平日 10:00 に --send
    LW_Send_Afternoon      毎平日 15:00 に --send
    LW_Cleanup_Reminder    毎月1日 10:05 に --cleanup-reminder
    LW_Holiday_Reminder    毎年 5/6   10:10 に --holiday-reminder（GW明け）

  使い方:
    管理者PowerShellで実行:
    .\schedule_lw_send.ps1
#>

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

$pw     = $PSScriptRoot
$script = Join-Path $pw 'lw_annotation_bot.py'

# Python 検索（GDX venv → system py）
$gdxVenv = Join-Path $pw '91GDX・252WORKNO-program\venv\Scripts\python.exe'
if (Test-Path -LiteralPath $gdxVenv) {
    $python = $gdxVenv
    $pyArgs = @()
} else {
    $python = 'py'
    $pyArgs = @('-3')
}

function Register-LwTask {
    param(
        [string]$TaskName,
        [string]$BotArg,
        [object]$Trigger,
        [string]$Description
    )
    $allArgs = $pyArgs + @($script, $BotArg)
    $action  = New-ScheduledTaskAction -Execute $python -Argument ($allArgs -join ' ') -WorkingDirectory $pw
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName    $TaskName `
        -Action      $action `
        -Trigger     $Trigger `
        -Settings    $settings `
        -Description $Description `
        -RunLevel    Highest `
        -Force | Out-Null
    Write-Host "登録完了: $TaskName"
}

# ── 10:00 毎日（土日・休暇はスクリプト内でスキップ） ─────────────────────────
$triggerMorning = New-ScheduledTaskTrigger -Daily -At "10:00"
Register-LwTask -TaskName "LW_Send_Morning" `
    -BotArg "--send" `
    -Trigger $triggerMorning `
    -Description "学習協力Bot 写真送信（朝）10:00 ／ 土日・休暇は自動スキップ"

# ── 15:00 毎日 ───────────────────────────────────────────────────────────────
$triggerAfternoon = New-ScheduledTaskTrigger -Daily -At "15:00"
Register-LwTask -TaskName "LW_Send_Afternoon" `
    -BotArg "--send" `
    -Trigger $triggerAfternoon `
    -Description "学習協力Bot 写真送信（午後）15:00 ／ 土日・休暇は自動スキップ"

# ── 毎月1日 10:05 削除リマインダー ──────────────────────────────────────────
$triggerCleanup = New-ScheduledTaskTrigger -Weekly -WeeksInterval 4 -DaysOfWeek Monday -At "10:05"
# 月1回トリガーは Monthly が正確だが ScheduledTasks モジュールに Monthly がないため
# schtasks で登録
$allArgsStr = ($pyArgs + @("`"$script`"", "--cleanup-reminder")) -join ' '
schtasks /Create /SC MONTHLY /D 1 /TN "LW_Cleanup_Reminder" `
    /TR "`"$python`" $allArgsStr" /ST "10:05" /RL HIGHEST /F | Out-Null
Write-Host "登録完了: LW_Cleanup_Reminder（毎月1日 10:05）"

# ── 毎年 5/6 10:10 休暇設定更新リマインダー ──────────────────────────────────
$allArgsStr2 = ($pyArgs + @("`"$script`"", "--holiday-reminder")) -join ' '
schtasks /Create /SC MONTHLY /D 6 /M MAY /TN "LW_Holiday_Reminder" `
    /TR "`"$python`" $allArgsStr2" /ST "10:10" /RL HIGHEST /F | Out-Null
Write-Host "登録完了: LW_Holiday_Reminder（毎年5/6 10:10）"

Write-Host ""
Write-Host "=== 登録済みタスク一覧 ==="
Get-ScheduledTask | Where-Object { $_.TaskName -like "LW_*" } |
    Select-Object TaskName, @{N="NextRun";E={(Get-ScheduledTaskInfo $_.TaskName).NextRunTime}} |
    Format-Table -AutoSize
