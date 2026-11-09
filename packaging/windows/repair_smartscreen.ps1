# Diagnose and clear SmartScreen / mark-of-the-web blocks on an installed toolbox.
# Usage:
#   powershell -ExecutionPolicy Bypass -File repair_smartscreen.ps1
#   powershell -ExecutionPolicy Bypass -File repair_smartscreen.ps1 -InstallRoot "D:\path\to\install"
# Messages are ASCII on purpose: Windows PowerShell 5.1 misreads BOM-less UTF-8.
param([string]$InstallRoot = '')
$ErrorActionPreference = 'Stop'

function Find-InstallRoot([string]$Requested) {
    if ($Requested) { return $Requested }
    $process = Get-Process -Name 'electricity-toolbox' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($process -and $process.Path) { return (Split-Path -Parent $process.Path) }
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles)) {
        if (-not $base -or -not (Test-Path -LiteralPath $base)) { continue }
        foreach ($dir in (Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue)) {
            if (Test-Path -LiteralPath (Join-Path $dir.FullName 'payload/manifest.json')) { return $dir.FullName }
        }
    }
    return ''
}

$root = Find-InstallRoot $InstallRoot
if (-not $root -or -not (Test-Path -LiteralPath $root)) {
    Write-Host 'INSTALL_ROOT=NOT_FOUND'
    Write-Host 'Re-run with -InstallRoot "<folder that contains payload\manifest.json>".'
    exit 1
}
Write-Host "INSTALL_ROOT=$root"

$manifest = Join-Path $root 'payload/manifest.json'
$info = $null
if (Test-Path -LiteralPath $manifest) {
    $info = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    Write-Host "PAYLOAD_VERSION=$($info.version)"
}

$checks = [ordered]@{
    'payload/runtime/python/python.exe' = Join-Path $root 'payload/runtime/python/python.exe'
    'payload/runtime/node/node.exe'     = Join-Path $root 'payload/runtime/node/node.exe'
    'electricity-toolbox.exe'           = Join-Path $root 'electricity-toolbox.exe'
    'WebView2Loader.dll'                = Join-Path $root 'WebView2Loader.dll'
}
if ($info) {
    $entry = @($info.files.PSObject.Properties | Where-Object { $_.Name -like '*/bin/toolbox-service.exe' })
    if ($entry.Count -eq 1) {
        $checks['payload/app bin/toolbox-service.exe'] = Join-Path $root ('payload/app/' + $entry[0].Name)
    }
}

$missing = @()
foreach ($name in $checks.Keys) {
    if (-not (Test-Path -LiteralPath $checks[$name])) { $missing += $name }
}
if ($missing.Count) { Write-Host ('MISSING=' + ($missing -join ', ')) } else { Write-Host 'MISSING=NONE' }

$cleared = 0
foreach ($path in $checks.Values) {
    if (Test-Path -LiteralPath $path) {
        $hadStream = Test-Path -LiteralPath ($path + ':Zone.Identifier')
        Unblock-File -LiteralPath $path -ErrorAction SilentlyContinue
        if ($hadStream) { $cleared++ }
    }
}
Write-Host "MOTW_CLEARED=$cleared"
Write-Host 'NEXT_STEPS:'
Write-Host ' 1. Windows Security -> Virus & threat protection -> Protection history: restore quarantined toolbox files.'
Write-Host ' 2. Add INSTALL_ROOT to Virus & threat protection exclusions.'
Write-Host ' 3. Right-click the full setup EXE -> Properties -> Unblock, then run it (or More info -> Run anyway).'
Write-Host ' 4. Re-run this script: MISSING should become NONE, then start the toolbox.'
