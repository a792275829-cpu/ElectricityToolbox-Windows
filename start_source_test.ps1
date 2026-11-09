param(
    [switch]$Smoke,
    [switch]$Console,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root

$entry = Join-Path $root '电力工具箱/toolbox_launcher.pyw'
$dataRoot = Join-Path $root '.toolbox/source-test/data'
$logDir = Join-Path $root 'logs/source-test'

$venvPython = Join-Path $root '.venv/Scripts/python.exe'
if ($Python) {
    $python = $Python
} elseif (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $python = ''
}

if (-not (Test-Path -LiteralPath $entry)) {
    throw "Toolbox launcher not found: $entry"
}

if ($Smoke) {
    Write-Output "TOOLBOX_SCRIPT=$entry"
    Write-Output "TOOLBOX_PYTHON=$python"
    Write-Output "TOOLBOX_CODE_ROOT=$root"
    Write-Output "TOOLBOX_DATA_ROOT=$dataRoot"
    Write-Output "TOOLBOX_LOG_DIR=$logDir"
    if (-not $python -or -not (Test-Path -LiteralPath $python)) {
        throw 'Python interpreter missing; run setup_windows.ps1 or pass -Python.'
    }
    & $python -c "import sys, tkinter, importlib.util; names = ('cryptography','docx','openpyxl','pandas','playwright','sklearn'); missing = [n for n in names if not importlib.util.find_spec(n)]; print('TOOLBOX_DEPS=' + ('OK' if not missing else 'MISSING:' + ','.join(missing))); sys.exit(1 if missing else 0)"
    if ($LASTEXITCODE -ne 0) { throw "Dependency check failed ($LASTEXITCODE)." }
    Write-Output 'TOOLBOX_SMOKE=OK'
    exit 0
}

if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw 'Python interpreter missing; run setup_windows.ps1 first, or pass -Python <path>.'
}

# Source checkout only: never start the packaged desktop executable.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$root;$env:PYTHONPATH" } else { $root }
$env:TOOLBOX_PYTHON = $python
$env:TOOLBOX_CODE_ROOT = $root
$env:TOOLBOX_DATA_ROOT = $dataRoot
$env:TOOLBOX_SOURCE_DEBUG = '1'

New-Item -ItemType Directory -Force -Path $dataRoot, $logDir | Out-Null

if ($Console) {
    Write-Host "Source test console: $entry"
    Write-Host "Test data: $dataRoot"
    & $python -u -X faulthandler $entry
    exit $LASTEXITCODE
}
$arguments = "-u -X faulthandler `"$entry`""

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$outLog = Join-Path $logDir "source-test-$stamp.out.log"
$errLog = Join-Path $logDir "source-test-$stamp.err.log"
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Start-Sleep -Milliseconds 1500
$process.Refresh()
if ($process.HasExited) {
    Write-Error "Source test GUI exited early (code $($process.ExitCode)); log: $errLog"
    if (Test-Path -LiteralPath $errLog) { Get-Content -LiteralPath $errLog -Tail 30 }
    exit 1
}
Write-Output "SOURCE_TEST_PID=$($process.Id)"
Write-Output "SOURCE_TEST_TITLE=$($process.MainWindowTitle)"
Write-Output "SOURCE_TEST_DATA=$dataRoot"
Write-Output "SOURCE_TEST_ERROR_LOG=$errLog"
