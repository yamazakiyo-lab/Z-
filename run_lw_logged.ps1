$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }
$hostName = $env:COMPUTERNAME
$pw = $PSScriptRoot

$logdir = Join-Path $pw 'lw_logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir -Force | Out-Null }

$lockDir = Join-Path $pw '.runtime'
if (-not (Test-Path $lockDir)) { New-Item -ItemType Directory -Path $lockDir -Force | Out-Null }
$lockPath = Join-Path $lockDir 'lw.lock'
$log = Join-Path $logdir ("lw_run_{0}_{1}.txt" -f $ts, $hostName)
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
        Write-Host "[SKIP] LW_Blob_Sync is already running. lock=$lockPath"
        exit 0
    }

    # Python 検索（GDX venv → system py）
    $gdxVenv = Join-Path $pw '91GDX・252WORKNO-program\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $gdxVenv) {
        $python = $gdxVenv
    } else {
        $python = 'py'
    }

    $script = Join-Path $pw 'lw_blob_sync.py'
    Write-Host "[LW] 同期開始: $ts"
    Push-Location $pw
    try {
        if ($python -eq 'py') {
            & $python -3 $script
        } else {
            & $python $script
        }
        if ($LASTEXITCODE -ne 0) {
            throw "lw_blob_sync.py exited with code $LASTEXITCODE"
        }
        Write-Host "[LW] 同期完了"
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
