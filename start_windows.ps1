param([switch]$Source)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = $root
$desktop = Join-Path $root 'desktop/src-tauri/target/release/electricity-toolbox.exe'
if (-not $Source -and (Test-Path -LiteralPath $desktop) -and (Test-Path -LiteralPath (Join-Path $root 'desktop/src-tauri/target/release/payload/manifest.json'))) {
    Start-Process -FilePath $desktop -WorkingDirectory $root -WindowStyle Hidden
    exit
}
$python = Join-Path $root '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup_windows.ps1 first.' }
$env:TOOLBOX_PYTHON = Join-Path $root '.venv/Scripts/python.exe'
$env:TOOLBOX_CODE_ROOT = $root
$entry = Join-Path $root '电力工具箱/toolbox_launcher.pyw'
Start-Process -FilePath $python -ArgumentList @('"' + $entry + '"') -WorkingDirectory $root -WindowStyle Hidden
