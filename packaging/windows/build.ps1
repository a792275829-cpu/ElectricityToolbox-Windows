param([Parameter(Mandatory=$true)][string]$PythonRoot, [string]$ForecastWorkbook, [switch]$FullOnly,
      [switch]$PruneArchive, [switch]$DryRun, [switch]$KeepGuard, [string]$ArchiveRoot,
      [switch]$DevelopmentBuild)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $root
# Preserve caller safeguards. -KeepGuard remains a compatible no-op.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PATH = "$env:USERPROFILE/.cargo/bin;$root/.venv/Scripts;$env:PATH"
$archiveRoot = if ($ArchiveRoot) { [IO.Path]::GetFullPath($ArchiveRoot) } else { Join-Path $root 'dist/build-archive' }
# Generated trees are replaced by renaming them aside, so the build stays correct
# even when the guard cannot be dropped; see packaging/windows/README.md.
function Archive-Generated([string]$LiteralPath, [string]$Name) {
    if (-not (Test-Path -LiteralPath $LiteralPath)) { return }
    $target = Join-Path $archiveRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    $destination = Join-Path $target $Name
    while (Test-Path -LiteralPath $destination) { $destination = "$destination-old" }
    Move-Item -LiteralPath $LiteralPath -Destination $destination
    Write-Host "Archived $Name -> $destination"
}
function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}
$arguments = @('packaging/windows/prepare_payload.py', '--python-root', $PythonRoot, '--archive-root', $archiveRoot)
if ($ForecastWorkbook) { $arguments += @('--forecast-workbook', $ForecastWorkbook) }
if ($DevelopmentBuild) { $arguments += '--development-build' }
if ($DryRun) {
    Run-Native "$root/.venv/Scripts/python.exe" @('packaging/windows/prepare_payload.py', '--dry-run', '--archive-root', $archiveRoot)
    Write-Host 'Windows dry run complete; no file was changed.'
    exit 0
}
# Refuse a version mismatch or empty release notes before changing generated trees.
if ($PruneArchive) {
    # Preview validates the cleanup boundary before an expensive build begins.
    Run-Native "$root/.venv/Scripts/python.exe" @('packaging/windows/prune_build_archive.py', '--root', $archiveRoot, '--keep', '1')
}
$sourceArgs = @('tools/release_source.py')
$versionArgs = @('packaging/tauri/version.py', 'check')
if ($DevelopmentBuild) { $sourceArgs += '--development' }
else { $versionArgs += '--require-notes' }
Run-Native "$root/.venv/Scripts/python.exe" $sourceArgs
Run-Native "$root/.venv/Scripts/python.exe" $versionArgs
# `.python-version` is the single source of truth for the packaged interpreter.
# Without this check the same version number could ship 3.11.15 from a local
# build and 3.12 from CI, and the difference only surfaces on a user's machine.
$expectedPython = (Get-Content -LiteralPath (Join-Path $root '.python-version') -Raw).Trim()
if (-not $expectedPython) { throw '.python-version is empty.' }
$runtimePython = Join-Path $PythonRoot 'python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) { throw "PythonRoot must contain python.exe: $PythonRoot" }
$actualPython = (& $runtimePython '-c' "import sys;print('.'.join(map(str,sys.version_info[:3])))").Trim()
if ($actualPython -ne $expectedPython) {
    throw "PythonRoot runs $actualPython but .python-version pins $expectedPython; use that CPython or update .python-version first."
}
Write-Host "Packaging runtime Python $actualPython (matches .python-version)"
Run-Native "$root/.venv/Scripts/python.exe" @('-m', 'pip', 'install', 'uv')
Run-Native "$root/.venv/Scripts/python.exe" $arguments
# prepare_payload.py rebuilds the Rust helper into the payload. Refuse to package
# a stale copy (for example a 0.1.34 helper inside a 0.1.35 installer).
Run-Native "$root/.venv/Scripts/python.exe" @('packaging/windows/verify_payload.py')
Archive-Generated (Join-Path $root 'desktop/node_modules') 'node_modules'
Run-Native 'npm.cmd' @('--prefix', 'desktop', 'ci')
Push-Location -LiteralPath (Join-Path $root 'desktop')
try { Run-Native 'npm.cmd' @('run', 'build', '--', '--bundles', 'nsis') }
finally { Pop-Location }
$output = if ($DevelopmentBuild) { Join-Path $root 'dist/windows-development' } else { Join-Path $root 'dist/windows' }
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
Run-Native "$root/.venv/Scripts/python.exe" ($sourceArgs + @('--output', (Join-Path $output "source-$version.json")))

if ($DevelopmentBuild) {
    Write-Host 'Development build only: do not publish; release delta generation is skipped.'
    if ($PruneArchive) {
        Run-Native "$root/.venv/Scripts/python.exe" @('packaging/windows/prune_build_archive.py', '--root', $archiveRoot, '--keep', '1', '--apply')
    }
    exit 0
}

$deltaArguments = @('packaging/windows/build_updates.py', '--installer', (Join-Path $output $downloadName), '--output', $output)
if ($FullOnly) { $deltaArguments += '--full-only' }
Run-Native "$root/.venv/Scripts/python.exe" $deltaArguments

# A release without this index silently disables the delta channel for every client.
$manifest = Join-Path $output "updates-$version/windows-update-manifest.json"
if (-not (Test-Path -LiteralPath $manifest)) { throw "Windows delta index was not produced: $manifest" }
Write-Host "Windows delta index: $manifest"
Get-ChildItem -LiteralPath (Split-Path -Parent $manifest) | Select-Object Name, Length | Format-Table -AutoSize
if ($PruneArchive) {
    Run-Native "$root/.venv/Scripts/python.exe" @('packaging/windows/prune_build_archive.py', '--root', $archiveRoot, '--keep', '1', '--apply')
} else {
    Write-Host "Build archive kept at $archiveRoot; pass -PruneArchive or run prune_build_archive.py to reclaim it."
}
