<#
  cleanup_logs_all.ps1 — ログ一括掃除（ノートローカル / K(デスクトップ) / Y 対応）

  実行マシンのローカルリポジトリ($PSScriptRoot)と、共有Yの Zフォルダ整理 のログを
  保持日数より古いものだけ削除する。
    - デスクトップ(KEIRI-PC)で実行 → ローカル=K(C:\Users\user\...\Zフォルダ整理) + Y を掃除
    - ノートで実行               → ローカル=ノートのZフォルダ整理 + Y を掃除
  両方で毎日走らせれば ノート・K・Y すべてに効く。

  使い方:
    powershell -NoProfile -ExecutionPolicy Bypass -File cleanup_logs_all.ps1
    powershell ... -File cleanup_logs_all.ps1 -RetainDays 7   # 保持日数を変更
    powershell ... -File cleanup_logs_all.ps1 -WhatIf         # 消さずに対象だけ表示
#>
param(
    [int]$RetainDays = 14,
    [switch]$WhatIf
)
$ErrorActionPreference = 'SilentlyContinue'
$cut = (Get-Date).AddDays(-$RetainDays)
$pw  = $PSScriptRoot
$yRoot = 'Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理'

# 掃除対象ディレクトリ（存在するものだけ／再帰）
$logDirs = @(
    (Join-Path $pw 'logs'),
    (Join-Path $pw 'lw_logs'),
    (Join-Path $pw 'archive\retention'),
    (Join-Path $yRoot 'logs'),
    (Join-Path $yRoot 'lw_logs')
)

# リポジトリ直下に溜まる生成ログのパターン
$rootPatterns = @(
    'photo_video_*.log','attention_general_*.txt','daily_runs_*.txt',
    'dailyrun_*.txt','*.log','usage_report_*.csv','signin_*.csv','name_match_report.csv'
)

$deleted = 0
$freedKB = 0

function Remove-Old($files) {
    foreach ($f in $files) {
        if ($f.LastWriteTime -lt $cut) {
            if ($WhatIf) {
                Write-Output ("[WHATIF] {0}  ({1:yyyy-MM-dd})" -f $f.FullName, $f.LastWriteTime)
            } else {
                $kb = [math]::Round($f.Length/1KB)
                Remove-Item -LiteralPath $f.FullName -Force
                if (-not (Test-Path -LiteralPath $f.FullName)) {
                    $script:deleted++; $script:freedKB += $kb
                }
            }
        }
    }
}

foreach ($d in $logDirs) {
    if (Test-Path -LiteralPath $d) {
        Remove-Old (Get-ChildItem -LiteralPath $d -File -Recurse -ErrorAction SilentlyContinue)
    }
}
foreach ($p in $rootPatterns) {
    Remove-Old (Get-ChildItem -LiteralPath $pw -Filter $p -File -ErrorAction SilentlyContinue)
}

$host2 = $env:COMPUTERNAME
if ($WhatIf) {
    Write-Output ("[cleanup_logs_all] WhatIf / RetainDays=$RetainDays / host=$host2")
} else {
    Write-Output ("[cleanup_logs_all] host=$host2 RetainDays=$RetainDays deleted=$deleted freed=$([math]::Round($freedKB/1024,1))MB at " + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
}
