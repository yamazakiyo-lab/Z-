if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}

# ドライラン判定（--dry-run フラグ）
$isDryRun = $args -contains '--dry-run'
# 強制実行判定（--force フラグ）
$isForce = $args -contains '--force'

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }
$hostName = $env:COMPUTERNAME
$pw = $PSScriptRoot
if (-not (Test-Path $pw)) { New-Item -ItemType Directory -Path $pw | Out-Null }
$logdir = Join-Path $pw 'logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }
# SYSTEM ユーザー（タスクスケジューラ）はマップドドライブにアクセスできないため
# C:\ProgramData を使用（SYSTEM・全ユーザー共通でアクセス可能）
$runtimeRoot = 'C:\ProgramData\tseg_vscode_runtime\gdx'
if (-not (Test-Path $runtimeRoot)) { New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null }
$env:GDX_RUNTIME_ROOT = $runtimeRoot

$lockDir = Join-Path $pw '.runtime'
if (-not (Test-Path $lockDir)) { New-Item -ItemType Directory -Path $lockDir -Force | Out-Null }
$lockPath = Join-Path $lockDir 'dailyrun.lock'
$log = Join-Path $logdir ("dailyrun_{0}_{1}.txt" -f $ts, $hostName)
$lockStream = $null

# ── 結果フラグ初期化 ──────────────────────────────────────
$results = @{
    GDX = 'UNKNOWN'
    OTHER = 'UNKNOWN'
    AzCopy = 'UNKNOWN'
}

Start-Transcript -Path $log -Force
try {
    if ($isDryRun) {
        Write-Host "[DRY-RUN] 本実行ではなくドライランモードで実行します"
    }
    
    try {
        # --force フラグがあれば古いロックを削除
        if ($isForce) {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
        }
        
        $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $lockStream.SetLength(0)
        $writer = New-Object System.IO.StreamWriter($lockStream, [System.Text.UTF8Encoding]::new($false), 1024, $true)
        $writer.WriteLine("host=$hostName")
        $writer.WriteLine("pid=$PID")
        $writer.WriteLine("started=$(Get-Date -Format s)")
        $writer.WriteLine("dryrun=$isDryRun")
        $writer.Flush()
        $writer.Dispose()
    } catch [System.IO.IOException] {
        if ($isForce) {
            Write-Host "[FORCE] 古いロックをスキップして新規実行"
        } else {
            Write-Host "[SKIP] DailyRun is already running on another host or process. lock=$lockPath"
            exit 0
        }
    }

    # ── ネットワークドライブ前処理（タスクスケジューラ/SYSTEM実行対策） ──
    # 非対話セッションではユーザーのドライブマッピングが存在しないため、UNCから再マップする
    $driveMap = @{ 'Z' = '\\192.168.2.252\共有'; 'Y' = '\\192.168.2.252\本社共有$' }
    foreach ($d in @($driveMap.Keys)) {
        if (-not (Test-Path -LiteralPath "${d}:\" -ErrorAction SilentlyContinue)) {
            Write-Host "[DRIVE] ${d}: が未接続のため再マップします -> $($driveMap[$d])"
            # net use は認証失敗時に入力待ちで無限ブロックするため使わない（260713のハング原因）。
            # New-SmbMapping は失敗時に即エラーを返す。
            try {
                New-SmbMapping -LocalPath "${d}:" -RemotePath $driveMap[$d] -Persistent $false -ErrorAction Stop | Out-Null
            } catch {
                Write-Warning "[DRIVE] ✗ ${d}: のマップに失敗: $($_.Exception.Message)"
            }
            if (Test-Path -LiteralPath "${d}:\" -ErrorAction SilentlyContinue) {
                Write-Host "[DRIVE] ✓ ${d}: マップ成功"
            } else {
                Write-Warning "[DRIVE] ✗ ${d}: が利用できません（実行ユーザーの認証情報を確認）"
            }
        }
    }
    if (-not (Test-Path -LiteralPath 'Z:\' -ErrorAction SilentlyContinue)) {
        Write-Warning "[DRIVE] Z: にアクセスできないため処理を中止します（空振り実行の防止）"
        exit 2
    }

    $launcher = 'py'
    $script = Join-Path $pw 'run_gdx.py'
    $gdxProjectDir = Get-ChildItem -LiteralPath $pw -Directory |
        Where-Object { $_.Name -like '91GDX*252WORKNO-program' } |
        Select-Object -First 1
    $venvPython = if ($gdxProjectDir) {
        Join-Path $gdxProjectDir.FullName 'venv\Scripts\python.exe'
    } else {
        $null
    }
    # Python 出力バッファリング無効化（Start-Transcript リアルタイム出力のため必須）
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONUTF8 = '1'
    # 文字化け対策: Python(UTF-8)出力を PowerShell 側も UTF-8 で受ける
    $env:PYTHONIOENCODING = 'utf-8'
    $OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    # ── リアルタイム Y ドライブログ同期ジョブ開始 ──────────────────────
    $yLogDir = 'Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理\logs'
    if ((Get-PSDrive Y -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath 'Y:\' -PathType Container -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path -LiteralPath $yLogDir)) { New-Item -ItemType Directory -Path $yLogDir -Force | Out-Null }
        # Start-Job は別プロセスで Y: マップドドライブにアクセスできないため UNC パスに変換
        $yDrive = Get-PSDrive Y -ErrorAction SilentlyContinue
        $yLogDirUNC = if ($yDrive -and $yDrive.DisplayRoot) {
            $rel = $yLogDir.Substring(2).TrimStart('\')
            Join-Path $yDrive.DisplayRoot $rel
        } else { $yLogDir }
        
        $syncLogJob = Start-Job -ScriptBlock {
            param($logdir, $yLogDir, $rootDir)
            while ($true) {
                try {
                    # FileShare.ReadWrite で Transcript ロック中でも読み取り可能
                    Get-ChildItem -LiteralPath $logdir -Filter "dailyrun_*.txt" -ErrorAction SilentlyContinue | ForEach-Object {
                        try {
                            $src = [System.IO.File]::Open($_.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                            $dst = [System.IO.File]::Open((Join-Path $yLogDir $_.Name), [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
                            try { $src.CopyTo($dst) } finally { $src.Dispose(); $dst.Dispose() }
                        } catch {}
                    }
                    # photo_video_*.log (91/general とも) はルートディレクトリに作成される
                    Get-ChildItem -LiteralPath $rootDir -Filter "photo_video_*.log" -ErrorAction SilentlyContinue |
                        Copy-Item -Destination $yLogDir -Force -ErrorAction SilentlyContinue
                    Get-ChildItem -LiteralPath $logdir -Filter "photo_video_*.log" -ErrorAction SilentlyContinue |
                        Copy-Item -Destination $yLogDir -Force -ErrorAction SilentlyContinue
                    # LW系ログ(lw_morning_run_*等)もノートからの監視用に同期
                    Get-ChildItem -LiteralPath $logdir -Filter "lw_*.txt" -ErrorAction SilentlyContinue |
                        Copy-Item -Destination $yLogDir -Force -ErrorAction SilentlyContinue
                } catch {
                    # ネットワークエラーはスキップ、処理は続行
                }
                Start-Sleep -Seconds 30
            }
        } -ArgumentList $logdir, $yLogDirUNC, $pw
    }

    Push-Location $pw
    try {
        # ── [1] GDX処理実行 ──────────────────────────────────────────────
        Write-Host "[GDX] GDXパイプライン開始"
        # パイプで1行ずつ受けて Write-Host することで Transcript にリアルタイムに落とす
        if ($venvPython -and (Test-Path -LiteralPath $venvPython)) {
            if ($isDryRun) {
                & $venvPython '-u' $script --dry-run 2>&1 | ForEach-Object { Write-Host "$_" }
            } else {
                & $venvPython '-u' $script 2>&1 | ForEach-Object { Write-Host "$_" }
            }
        } else {
            if ($isDryRun) {
                & $launcher -3 '-u' $script --dry-run 2>&1 | ForEach-Object { Write-Host "$_" }
            } else {
                & $launcher -3 '-u' $script 2>&1 | ForEach-Object { Write-Host "$_" }
            }
        }
        if ($LASTEXITCODE -eq 0) {
            $results.GDX = 'PASS'
            Write-Host "[GDX] ✓ GDXパイプライン完了"
        } else {
            $results.GDX = 'FAIL'
            Write-Warning "[GDX] ✗ run_gdx.py がエラーで終了しました (code $LASTEXITCODE)。以降のステップは続行します。"
        }

        # ── [2] OTHER処理実行 ────────────────────────────────────────────
        $otherScript = Join-Path $pw 'run_91other.py'
        if (Test-Path -LiteralPath $otherScript) {
            Write-Host "[OTHER] 91OTHER処理開始"
            if ($isDryRun) {
                Write-Host "[OTHER] [DRY-RUN] 実行スキップ"
                $results.OTHER = 'SKIP'
            } else {
                $otherTimeout = 1800  # 無出力がこの秒数続いたらハング判定（進捗出力がある限り切らない）
                $otherStartTime = Get-Date
                try {
                    $isVenv = ($venvPython -and (Test-Path -LiteralPath $venvPython))
                    $otherExe = if ($isVenv) { $venvPython } else { $launcher }
                    # Start-Job で実行し Receive-Job で 5 秒ごとにトランスクリプトへ転送
                    $otherJob = Start-Job -ScriptBlock {
                        param($exe, $script, $useVenv)
                        $env:PYTHONUNBUFFERED = '1'
                        $env:PYTHONUTF8 = '1'
                        $env:PYTHONIOENCODING = 'utf-8'
                        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
                        if ($useVenv) {
                            & $exe '-u' $script 2>&1
                        } else {
                            & $exe '-3' '-u' $script 2>&1
                        }
                        "___EXITCODE___:$LASTEXITCODE"
                    } -ArgumentList $otherExe, $otherScript, $isVenv
                    # 無出力監視方式: 出力が続く限りタイマーをリセット（正常進行中は切らない）
                    $lastOutputTime = Get-Date
                    $otherExit = $null
                    do {
                        Start-Sleep -Seconds 5
                        $chunk = @(Receive-Job -Id $otherJob.Id -ErrorAction SilentlyContinue)
                        if ($chunk.Count -gt 0) { $lastOutputTime = Get-Date }
                        $chunk | ForEach-Object {
                            # 終了コード行はポーリング中に届くことがあるため、捨てずに記録する(260715修正)
                            if ($_ -match '^___EXITCODE___:(\d+)') { $otherExit = [int]$Matches[1] }
                            else { Write-Host $_ }
                        }
                        if (((Get-Date) - $lastOutputTime).TotalSeconds -gt $otherTimeout) {
                            Stop-Job $otherJob
                            $results.OTHER = 'TIMEOUT'
                            Write-Warning "[OTHER] ⏱ 91OTHER処理が無出力${otherTimeout}秒でタイムアウト（ハング判定）しました。"
                            break
                        }
                    } until ((Get-Job -Id $otherJob.Id).State -in 'Completed','Failed','Stopped')
                    # 初期値'UNKNOWN'はtruthyのため -not では判定できない(260715修正:
                    # 完走してもUNKNOWN表示のままだったバグの本体)
                    if ($results.OTHER -eq 'UNKNOWN') {
                        $remaining = @(Receive-Job -Id $otherJob.Id -ErrorAction SilentlyContinue)
                        $remaining | ForEach-Object {
                            if ($_ -match '^___EXITCODE___:(\d+)') { $otherExit = [int]$Matches[1] }
                            else { Write-Host $_ }
                        }
                        if ($null -eq $otherExit) { $otherExit = 1 }
                        $exitCode = $otherExit
                        if ($exitCode -eq 0) {
                            $results.OTHER = 'PASS'
                            Write-Host "[OTHER] ✓ 91OTHER処理完了"
                        } else {
                            $results.OTHER = 'FAIL'
                            Write-Warning "[OTHER] ✗ run_91other.py がエラーで終了しました (code $exitCode)。以降のステップは続行します。"
                        }
                    }
                    Remove-Job $otherJob -Force -ErrorAction SilentlyContinue
                } catch {
                    Write-Warning "[OTHER] ✗ 予期しないエラーが発生しました: $_"
                    $results.OTHER = 'ERROR'
                }
            }
        } else {
            Write-Warning "[OTHER] run_91other.py が見つかりません。スキップします。"
            $results.OTHER = 'SKIP'
        }
        $ragDir = $pw  # $PSScriptRoot（UNCパス）を使用。$env:USERPROFILEはデスクトップPCのローカルパスになるため不可
        $ragPython = Join-Path $ragDir 'rag_venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $ragPython)) {
            $ragPython = Join-Path $ragDir '91GDX・252WORKNO-program\venv\Scripts\python.exe'
        }
        if (-not (Test-Path -LiteralPath $ragPython)) { $ragPython = $venvPython }

        Write-Host "[RAG] インデックス更新開始"
        Push-Location $ragDir
        try {
            & $ragPython (Join-Path $ragDir 'run_rag_index.py')
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "[RAG] run_rag_index.py がエラーで終了しました (code $LASTEXITCODE)"
            }

            # ── RAG: 説明文生成（新規分のみ） ─────────────────────────
            Write-Host "[RAG] 説明文生成開始（新規分のみ）"
            & $ragPython (Join-Path $ragDir 'run_rag_describe.py')
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "[RAG] run_rag_describe.py がエラーで終了しました (code $LASTEXITCODE)"
            }
        } finally {
            Pop-Location
        }

        # ── [3] AzCopy: Blob Storage 同期（差分のみ） ────────────────────
        Write-Host "[AZCOPY] Blob Storage 同期開始"
        $azCopy = 'C:\azcopy\azcopy_windows_amd64_10.32.4\azcopy.exe'
        $azCopyCount = 0
        $azCopyFailed = $false
        $blobSasToken = $env:AZURE_BLOB_SAS_TOKEN
        if (-not $blobSasToken) {
            # .env から読み込み
            $envFile = Join-Path $ragDir '.env'
            if (Test-Path $envFile) {
                Get-Content $envFile | ForEach-Object {
                    if ($_ -match '^AZURE_BLOB_SAS_TOKEN=(.+)$') {
                        $blobSasToken = $Matches[1].Trim('"').Trim("'")
                    }
                }
            }
        }
        if ((Test-Path -LiteralPath $azCopy) -and $blobSasToken) {
            Write-Host "[AZCOPY] Blob Storage 同期開始"
            $src = 'Z:\takachiho\2to9_業務別フォルダ\91_工番別実績写真・動画'
            $dst = "https://tsegphotos.blob.core.windows.net/photos?$blobSasToken"
            & $azCopy sync $src $dst --recursive
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "[AZCOPY] 同期がエラーで終了しました (code $LASTEXITCODE)"
                $azCopyFailed = $true
            } else {
                Write-Host "[AZCOPY] Blob Storage 同期完了"
            }
        } else {
            Write-Warning "[AZCOPY] azcopy.exe または SAS トークンが見つかりません。スキップします。"
        }

        # ── AzCopy: 271_修理工事指令書 PDF Blob 同期 ─────────────────
        $blobSasToken271 = $env:AZURE_BLOB_271_SAS_TOKEN
        if (-not $blobSasToken271) {
            $envFile = Join-Path $ragDir '.env'
            if (Test-Path $envFile) {
                Get-Content $envFile | ForEach-Object {
                    if ($_ -match '^AZURE_BLOB_271_SAS_TOKEN=(.+)$') {
                        $blobSasToken271 = $Matches[1].Trim('"').Trim("'")
                    }
                }
            }
        }
        # 専用トークンが無ければ共通トークンにフォールバック
        if (-not $blobSasToken271) { $blobSasToken271 = $blobSasToken }
        if ((Test-Path -LiteralPath $azCopy) -and $blobSasToken271) {
            Write-Host "[AZCOPY:271] 指令書PDF Blob Storage 同期開始"
            $src271 = 'Z:\takachiho\2to9_業務別フォルダ\27_サービス・出張工事\271_修理工事指令書'
            $dst271 = "https://tsegphotos.blob.core.windows.net/shirei-pdf?$blobSasToken271"
            & $azCopy sync $src271 $dst271 --recursive --include-pattern "*.pdf;*.PDF"
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "[AZCOPY:271] 同期がエラーで終了しました (code $LASTEXITCODE)"
                $azCopyFailed = $true
            } else {
                Write-Host "[AZCOPY:271] 指令書PDF Blob Storage 同期完了"
            }
        } else {
            Write-Warning "[AZCOPY:271] azcopy.exe または271用SASトークンが見つかりません。スキップします。"
        }

        # ── LW: LINE WORKS Blob 同期 ──────────────────────────────────
        $lwScript = Join-Path $pw 'lw_blob_sync.py'
        $lwPython = if ($venvPython -and (Test-Path -LiteralPath $venvPython)) { $venvPython } else { 'py' }
        if (Test-Path -LiteralPath $lwScript) {
            Write-Host "[LW] LINE WORKS Blob 同期開始"
            Push-Location $pw
            try {
                if ($lwPython -eq 'py') {
                    & $lwPython -3 $lwScript
                } else {
                    & $lwPython $lwScript
                }
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "[LW] lw_blob_sync.py がエラーで終了しました (code $LASTEXITCODE)"
                } else {
                    Write-Host "[LW] LINE WORKS Blob 同期完了"
                }
            } finally {
                Pop-Location
            }
        } else {
            Write-Warning "[LW] lw_blob_sync.py が見つかりません。スキップします。"
        }

        # ── LW: _LWExtraction → B4 振り分け ──────────────────────────────
        $ldSortScript = Join-Path $pw 'ld_sort.py'
        if (Test-Path -LiteralPath $ldSortScript) {
            Write-Host "[LW] LWExtraction -> B4 振り分け開始"
            Push-Location $pw
            try {
                if ($lwPython -eq 'py') {
                    & $lwPython -3 $ldSortScript
                } else {
                    & $lwPython $ldSortScript
                }
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "[LW] ld_sort.py がエラーで終了しました (code $LASTEXITCODE)"
                } else {
                    Write-Host "[LW] LWExtraction -> B4 振り分け完了"
                }
            } finally {
                Pop-Location
            }
        } else {
            Write-Warning "[LW] ld_sort.py が見つかりません。スキップします。"
        }

        # ── 学習協力Bot: Blob アノテーション同期 → .json サイドカー作成 ───────
        $annBotScript = Join-Path $pw 'lw_annotation_bot.py'
        if (Test-Path -LiteralPath $annBotScript) {
            Write-Host "[BOT] アノテーション同期開始"
            Push-Location $pw
            try {
                if ($lwPython -eq 'py') {
                    & $lwPython -3 $annBotScript --sync-annotations
                } else {
                    & $lwPython $annBotScript --sync-annotations
                }
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "[BOT] lw_annotation_bot.py --sync-annotations がエラー (code $LASTEXITCODE)"
                } else {
                    Write-Host "[BOT] アノテーション同期完了"
                }

                # --send は専用タスク（LW_Send_Morning / LW_Send_Afternoon）で実行するためここでは行わない
            } finally {
                Pop-Location
            }
        } else {
            Write-Warning "[BOT] lw_annotation_bot.py が見つかりません。スキップします。"
        }

        # ── 工番マスタを Blob にエクスポート(受信Botの工番チェック用) ──────
        if (-not $isDryRun) {
            $exportScript = Join-Path $pw 'export_workno_master.py'
            if (Test-Path -LiteralPath $exportScript) {
                Write-Host "[MASTER] 工番マスタを Blob にエクスポート中..."
                $exportPython = if ($ragPython -and (Test-Path -LiteralPath $ragPython)) { $ragPython } else { $launcher }
                & $exportPython $exportScript 2>&1 | ForEach-Object { Write-Host $_ }
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[MASTER] ✓ 工番マスタ エクスポート完了"
                } else {
                    Write-Warning "[MASTER] ✗ 工番マスタ エクスポート失敗 (code $LASTEXITCODE)"
                }
            }
        }

        # ── AzCopyフラグ判定 ─────────────────────────────────────────
        if ($isDryRun) {
            $results.AzCopy = 'SKIP'
        } elseif (-not $azCopyFailed) {
            $results.AzCopy = 'PASS'
        } else {
            $results.AzCopy = 'FAIL'
        }

        # ── [RESULT] 結果サマリー出力 ──────────────────────────────────
        $summaryMsg = "[RESULT] GDX=$($results.GDX), OTHER=$($results.OTHER), AzCopy=$($results.AzCopy)"
        Write-Host ""
        Write-Host "════════════════════════════════════════════════════════"
        Write-Host $summaryMsg
        Write-Host "════════════════════════════════════════════════════════"
        
        # ドライランモードの場合は表示
        if ($isDryRun) {
            Write-Host "[DRY-RUN] ドライランモードで実行しました"
        }

        # タスクステータスを Blob に書く
        if (-not $isDryRun) {
            $gdxOverall = if ($results.GDX -eq 'PASS' -and $results.AzCopy -ne 'FAIL') { 'PASS' } else { 'FAIL' }
            $statusMsg = "GDX=$($results.GDX), OTHER=$($results.OTHER), AzCopy=$($results.AzCopy)"
            # SYSTEM ユーザーでは 'py' が PATH にないため $ragPython を使用
            $statusPython = if ($ragPython -and (Test-Path -LiteralPath $ragPython)) { $ragPython } else { $launcher }
            Write-Host "[STATUS] タスクステータスを Blob に書き込み中..."
            & $statusPython (Join-Path $pw 'write_task_status.py') --task gdx --status $gdxOverall --message $statusMsg
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[STATUS] ✓ Blob 書き込み完了"
            } else {
                Write-Warning "[STATUS] ✗ Blob 書き込み失敗 (code $LASTEXITCODE)"
            }
        }
    } finally {
        Pop-Location
    }
} catch {
    Write-Error $_
} finally {
    if ($lockStream) {
        try { $lockStream.Dispose() } catch {}
    }
    Stop-Transcript
    
    # ── リアルタイム同期ジョブ停止 ────────────────────────────────────
    if ($syncLogJob) {
        try {
            Stop-Job -Job $syncLogJob -ErrorAction SilentlyContinue
            Remove-Job -Job $syncLogJob -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    
    # Y: ドライブにもコピー（完了確認用）
    $yLogDir = 'Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理\logs'
    if ((Get-PSDrive Y -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath 'Y:\' -PathType Container -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path -LiteralPath $yLogDir)) {
            New-Item -ItemType Directory -Path $yLogDir -Force | Out-Null
        }
        # dailyrun_*.txt をコピー
        try { Copy-Item -LiteralPath $log -Destination $yLogDir -Force } catch {}
        # photo_video_*.log (91/general) もコピー（ルートと logs 両方から）
        foreach ($searchDir in @($pw, $logdir)) {
            Get-ChildItem -LiteralPath $searchDir -Filter "photo_video_*.log" -ErrorAction SilentlyContinue | ForEach-Object {
                try { Copy-Item -LiteralPath $_.FullName -Destination $yLogDir -Force } catch {}
            }
        }
    }

    # ── 古いログの削除（保持期間: 7日、Y: とローカル両方） ──────────────
    $logRetentionDays = 7
    $cutoff = (Get-Date).AddDays(-$logRetentionDays)
    $logPatterns = @('dailyrun_*.txt', 'photo_video_*.log', 'lw_*.txt')
    $cleanupDirs = @($yLogDir, $logdir, $pw) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    foreach ($dir in $cleanupDirs) {
        foreach ($pattern in $logPatterns) {
            Get-ChildItem -LiteralPath $dir -Filter $pattern -File -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $cutoff } |
                ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -Force } catch {} }
        }
    }
}

