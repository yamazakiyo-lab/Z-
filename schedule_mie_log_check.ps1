# DailyRun ログ確認タスク登録スクリプト（12:00 実行）
# CHECK_DAILYRUNS: GDX, OTHER, AzCopy の実行結果をチェック

$pw = $PSScriptRoot
$logCheckScript = Join-Path $pw "check_dailyrun_log.bat"

Write-Host "Registering scheduled task 'CHECK_DAILYRUNS' to run daily at 12:00"

try {
	$action = New-ScheduledTaskAction -Execute $logCheckScript
	$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 12 -Minute 0 -Second 0)
	Register-ScheduledTask -TaskName "CHECK_DAILYRUNS" -Action $action -Trigger $trigger -RunLevel Highest -Force
	Write-Host "✅ Scheduled task 'CHECK_DAILYRUNS' registered successfully at 12:00"
	Write-Host "To remove: schtasks /Delete /TN 'CHECK_DAILYRUNS' /F"
} catch {
	Write-Host "Failed to register using ScheduledTasks cmdlets. Error: $_"
	$tr = '"' + $logCheckScript + '"'
	schtasks /Create /SC DAILY /TN "CHECK_DAILYRUNS" /TR $tr /ST 12:00 /RL HIGHEST /F
	Write-Host "✅ Fallback registration attempted at 12:00"
}
