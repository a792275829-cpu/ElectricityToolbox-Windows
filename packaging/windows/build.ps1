param([Parameter(Mandatory=$true)][string]$PythonRoot, [string]$ForecastWorkbook, [switch]$FullOnly)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PATH = "$env:USERPROFILE/.cargo/bin;$root/.venv/Scripts;$env:PATH"
function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}
$arguments = @('packaging/windows/prepare_payload.py', '--python-root', $PythonRoot)
if ($ForecastWorkbook) { $arguments += @('--forecast-workbook', $ForecastWorkbook) }
Run-Native "$root/.venv/Scripts/python.exe" @('-m', 'pip', 'install', 'uv')
Run-Native "$root/.venv/Scripts/python.exe" $arguments
Run-Native 'npm.cmd' @('--prefix', 'desktop', 'ci')
Push-Location -LiteralPath (Join-Path $root 'desktop')
try { Run-Native 'npm.cmd' @('run', 'build', '--', '--bundles', 'nsis') }
finally { Pop-Location }
$output = Join-Path $root 'dist/windows'
New-Item -ItemType Directory -Force $output | Out-Null
$targetRoot = if ($env:CARGO_TARGET_DIR) { [IO.Path]::GetFullPath($env:CARGO_TARGET_DIR) } else { "$root/desktop/src-tauri/target" }
$version = (Get-Content -LiteralPath "$root/desktop/package.json" -Raw | ConvertFrom-Json).version
$downloadName = "ElectricityToolbox_${version}_windows_x64_setup.exe"
$installers = @(Get-ChildItem -LiteralPath "$targetRoot/release/bundle/nsis" -Filter "*_${version}_x64-setup.exe")
if ($installers.Count -ne 1) { throw 'NSIS build must produce exactly one installer for the current version.' }
$installers | ForEach-Object {
    $destination = Join-Path $output $downloadName
    if (Test-Path -LiteralPath $destination) { throw "Versioned installer already exists: $destination" }
    Copy-Item -LiteralPath $_.FullName -Destination $destination
    $digest = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$destination.sha256", "$digest  $downloadName`n")
}
Write-Host "Windows installer and SHA256: $output"

$deltaArguments = @('packaging/windows/build_updates.py', '--installer', (Join-Path $output $downloadName), '--output', $output)
if ($FullOnly) { $deltaArguments += '--full-only' }
Run-Native "$root/.venv/Scripts/python.exe" $deltaArguments
