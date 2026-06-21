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
$lockPath = Join-Path $lockDir 'gdx.lock'
$log = Join-Path $logdir ("gdx_run_{0}_{1}.txt" -f $ts, $hostName)
$lockStream = $null

Start-Transcript -Path $log -Force
try {
    try {
        $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $lockStream.SetLength(0)
        $writer = New-Object System.IO.StreamWriter($lockStream, [System.Text.UTF8Encoding]::new($false), 1024, $true)
        $writer.WriteLine("host=$hostName")
        $writer.WriteLine("pid=$PID")
        $writer.WriteLine("started=$(Get-Date -Format s)")
        $writer.Flush()
        $writer.Dispose()
    } catch [System.IO.IOException] {
        Write-Host "[SKIP] GDX is already running on another host or process. lock=$lockPath"
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
        if ($venvPython -and (Test-Path -LiteralPath $venvPython)) {
            & $venvPython $script
        } else {
            & $launcher -3 $script
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "[GDX] run_gdx.py がエラーで終了しました (code $LASTEXITCODE)。RAG/AzCopy は続行します。"
        }

        # ── RAG: インデックス更新 ──────────────────────────────────────
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

        # ── AzCopy: Blob Storage 同期（差分のみ） ─────────────────────
        $azCopy = 'C:\azcopy\azcopy_windows_amd64_10.32.4\azcopy.exe'
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
            } else {
                Write-Host "[AZCOPY] Blob Storage 同期完了"
            }
        } else {
            Write-Warning "[AZCOPY] azcopy.exe または SAS トークンが見つかりません。スキップします。"
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

        # ── LW: _LDExtraction → B4 振り分け ──────────────────────────────
        $ldSortScript = Join-Path $pw 'ld_sort.py'
        if (Test-Path -LiteralPath $ldSortScript) {
            Write-Host "[LW] LDExtraction -> B4 振り分け開始"
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
                    Write-Host "[LW] LDExtraction -> B4 振り分け完了"
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
}
