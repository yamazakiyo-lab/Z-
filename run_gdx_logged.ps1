if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
}

# ドライラン判定（--dry-run フラグ）
$isDryRun = $args -contains '--dry-run'

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }
$hostName = $env:COMPUTERNAME
$pw = $PSScriptRoot
if (-not (Test-Path $pw)) { New-Item -ItemType Directory -Path $pw | Out-Null }
$logdir = Join-Path $pw 'logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }
$runtimeBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
$runtimeRoot = Join-Path $runtimeBase 'tseg_vscode_runtime\gdx'
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
        Write-Host "[SKIP] DailyRun is already running on another host or process. lock=$lockPath"
        exit 0
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
    Push-Location $pw
    try {
        # ── [1] GDX処理実行 ──────────────────────────────────────────────
        Write-Host "[GDX] GDXパイプライン開始"
        if ($venvPython -and (Test-Path -LiteralPath $venvPython)) {
            if ($isDryRun) {
                & $venvPython $script --dry-run
            } else {
                & $venvPython $script
            }
        } else {
            if ($isDryRun) {
                & $launcher -3 $script --dry-run
            } else {
                & $launcher -3 $script
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
                $otherTimeout = 1800  # 30分
                $otherStartTime = Get-Date
                try {
                    if ($venvPython -and (Test-Path -LiteralPath $venvPython)) {
                        $proc = Start-Process -FilePath $venvPython -ArgumentList $otherScript -PassThru -NoNewWindow
                    } else {
                        $proc = Start-Process -FilePath $launcher -ArgumentList @('-3', $otherScript) -PassThru -NoNewWindow
                    }
                    if ($proc.WaitForExit($otherTimeout * 1000)) {
                        # 完了
                        if ($proc.ExitCode -eq 0) {
                            $results.OTHER = 'PASS'
                            Write-Host "[OTHER] ✓ 91OTHER処理完了"
                        } else {
                            $results.OTHER = 'FAIL'
                            Write-Warning "[OTHER] ✗ run_91other.py がエラーで終了しました (code $($proc.ExitCode))。以降のステップは続行します。"
                        }
                    } else {
                        # タイムアウト
                        Write-Warning "[OTHER] ⏱ 91OTHER処理がタイムアウト（30分以上）しました。プロセスを強制終了します。"
                        $proc.Kill()
                        $results.OTHER = 'TIMEOUT'
                    }
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
    # Y: ドライブにもコピー（ラップトップから確認できるよう）
    $yLogDir = 'Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理\gdx_logs'
    if ((Get-PSDrive Y -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath 'Y:\' -PathType Container -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path -LiteralPath $yLogDir)) {
            New-Item -ItemType Directory -Path $yLogDir -Force | Out-Null
        }
        try { Copy-Item -LiteralPath $log -Destination $yLogDir -Force } catch {}
    }
}

