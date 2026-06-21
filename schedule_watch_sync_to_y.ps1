param()

$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }

$TaskName = 'WATCH_SYNC_TO_Y'
$Script   = 'C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理\watch_and_sync_to_y.ps1'

# 既存タスク確認
$existing = schtasks /Query /TN $TaskName /FO LIST /V 2>$null | Out-String
if ($existing -match 'Status:\s+Running') {
    Write-Host "タスク '$TaskName' はすでに実行中です。"
    exit 0
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"" + $Script + "`"")

$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action   $action `
        -Trigger  $trigger `
        -Settings $settings `
        -RunLevel Highest `
        -Force -ErrorAction Stop | Out-Null
    Write-Host "タスク '$TaskName' を登録しました。"
} catch {
    Write-Host "Register-ScheduledTask 失敗 → schtasks でフォールバック登録中..."
    $tr = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Script + '"'
    schtasks /Create /SC ONLOGON /TN $TaskName /TR $tr /RL HIGHEST /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "schtasks で登録しました。"
    } else {
        Write-Host "登録失敗。管理者権限で実行してください。"
        exit 1
    }
}

Write-Host "今すぐ起動する場合:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
