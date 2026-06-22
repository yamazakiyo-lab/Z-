# GDX ログ確認タスク登録スクリプト（12:00 実行）

$pw = $PSScriptRoot
$logCheckScript = Join-Path $pw "check_gdx_log.bat"

Write-Host "Registering scheduled task 'GDX_Check_Log' to run daily at 12:00"

try {
	$action = New-ScheduledTaskAction -Execute $logCheckScript
	$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date -Hour 12 -Minute 0 -Second 0)
	Register-ScheduledTask -TaskName "GDX_Check_Log" -Action $action -Trigger $trigger -RunLevel Highest -Force
	Write-Host "✅ Scheduled task 'GDX_Check_Log' registered successfully at 12:00"
	Write-Host "To remove: schtasks /Delete /TN 'GDX_Check_Log' /F"
} catch {
	Write-Host "Failed to register using ScheduledTasks cmdlets. Error: $_"
	$tr = '"' + $logCheckScript + '"'
	schtasks /Create /SC DAILY /TN "GDX_Check_Log" /TR $tr /ST 12:00 /RL HIGHEST /F
	Write-Host "✅ Fallback registration attempted at 12:00"
}
