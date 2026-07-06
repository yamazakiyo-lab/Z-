$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$utf8Init = Join-Path $PSScriptRoot 'ps_utf8_init.ps1'
if (Test-Path -LiteralPath $utf8Init) { . $utf8Init }
$hostName = $env:COMPUTERNAME
$pw = $PSScriptRoot
if (-not (Test-Path $pw)) { New-Item -ItemType Directory -Path $pw | Out-Null }
$logdir = Join-Path $pw 'logs'
if (-not (Test-Path $logdir)) { New-Item -ItemType Directory -Path $logdir | Out-Null }

$lockDir = Join-Path $pw '.runtime'
if (-not (Test-Path $lockDir)) { New-Item -ItemType Directory -Path $lockDir -Force | Out-Null }
$lockPath = Join-Path $lockDir 'other.lock'
$log = Join-Path $logdir ("other_run_{0}_{1}.txt" -f $ts, $hostName)
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
        Write-Host "[SKIP] 91OTHER is already running on another host or process. lock=$lockPath"
        exit 0
    }

    $launcher = 'py'
    $script = Join-Path $pw 'run_91other.py'
    Push-Location $pw
    try {
        & $launcher -3 $script
        if ($LASTEXITCODE -ne 0) {
            throw "run_91other.py exited with code $LASTEXITCODE"
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
    $yLogDir = 'Y:\管理本部\情報管理課\tseg_vscode\Zフォルダ整理\other_logs'
    if ((Get-PSDrive Y -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath 'Y:\' -PathType Container -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path -LiteralPath $yLogDir)) {
            New-Item -ItemType Directory -Path $yLogDir -Force | Out-Null
        }
        try { Copy-Item -LiteralPath $log -Destination $yLogDir -Force } catch {}
    }
}
