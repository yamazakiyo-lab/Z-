param(
    [string]$DeployPath = 'C:\Deploy\gdx_workspace',
    [switch]$CreateTasks
)

# deploy_windows_template.ps1
# サーバー上で実行する想定のセットアップテンプレート。

Write-Output "Preparing deploy path: $DeployPath"
if (-not (Test-Path $DeployPath)) { New-Item -ItemType Directory -Path $DeployPath | Out-Null }

# 仮想環境作成
if (-not (Test-Path (Join-Path $DeployPath 'venv'))) {
    py -3 -m venv (Join-Path $DeployPath 'venv')
}

# 依存インストール（requirements.txt がある前提）
$req = Join-Path $DeployPath 'requirements.txt'
if (Test-Path $req) {
    & (Join-Path $DeployPath 'venv\Scripts\pip.exe') install -r $req
}

# logs ディレクトリ
$logDir = Join-Path $DeployPath 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

if ($CreateTasks) {
    Write-Output 'Creating scheduled tasks (local user)...'
    $gdx = "schtasks /Create /TN `"GDX_DailyRun`" /SC DAILY /ST 00:00 /TR `"$DeployPath\\run_gdx_wrapper.bat`" /F"
    $other = "schtasks /Create /TN `"OTHER_DailyRun`" /SC DAILY /ST 00:00 /TR `"$DeployPath\\run_91other_wrapper.bat`" /F"
    $check = "schtasks /Create /TN `"CHECK_DAILYRUNS`" /SC DAILY /ST 00:01 /TR `"powershell -NoProfile -ExecutionPolicy Bypass -File '$DeployPath\\check_and_cleanup_logs.ps1'`" /F"
    iex $gdx; iex $other; iex $check
}

Write-Output 'Deploy template finished.'
