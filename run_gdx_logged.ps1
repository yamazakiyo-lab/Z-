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
        if (Test-Path -LiteralPath $venvPython) {
            & $venvPython $script
        } else {
            & $launcher -3 $script
        }
        if ($LASTEXITCODE -ne 0) {
            throw "run_gdx.py exited with code $LASTEXITCODE"
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
