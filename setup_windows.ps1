param([switch]$SkipBrowser, [switch]$SkipNative)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $root
function Run-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Run-Native 'py' @('-3', '-m', 'venv', (Join-Path $root '.venv'))
    } else {
        Run-Native 'python' @('-m', 'venv', (Join-Path $root '.venv'))
    }
}
Run-Native $python @('-c', 'import sys,tkinter; assert sys.version_info >= (3,11), "Python 3.11+ required"')
Run-Native $python @('-m', 'pip', 'install', '-r', (Join-Path $root 'packaging/windows/requirements.txt'))
if (-not $SkipBrowser) { Run-Native $python @('-m', 'playwright', 'install', 'chromium') }
$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargo -and (Test-Path -LiteralPath "$env:USERPROFILE/.cargo/bin/cargo.exe")) {
    $env:PATH = "$env:USERPROFILE/.cargo/bin;$env:PATH"
    $cargo = Get-Command cargo
}
if (-not $SkipNative) {
    if (-not $cargo) { throw 'Install Rust stable (MSVC) and Visual Studio C++ Build Tools, then rerun setup_windows.ps1.' }
    Run-Native $python @('native/price-xlsx/build.py')
}
$nodeVersion = '22.23.3'
$versionFile = Join-Path $root 'packaging/tauri/build_support.py'
if (Test-Path -LiteralPath $versionFile) {
    $match = Select-String -LiteralPath $versionFile -Pattern '^NODE_VERSION\s*=\s*"([^"]+)"'
    if ($match) { $nodeVersion = $match.Matches[0].Groups[1].Value }
}
$nodeDir = Join-Path $root 'runtime/node'
$nodeExe = Join-Path $nodeDir 'node.exe'
if (-not (Test-Path -LiteralPath $nodeExe)) {
    $cache = Join-Path $root 'dist/windows-build'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archiveName = "node-v$nodeVersion-win-x64"
    $archive = Join-Path $cache "$archiveName.zip"
    $base = "https://nodejs.org/dist/v$nodeVersion/"
    try {
        $sums = Join-Path $cache 'SHASUMS256.txt'
        Invoke-WebRequest -UseBasicParsing -Uri ($base + 'SHASUMS256.txt') -OutFile $sums
        $expected = ((Get-Content -LiteralPath $sums | Where-Object { $_ -match [regex]::Escape("$archiveName.zip") }) -split '\s+')[0]
        if (-not (Test-Path -LiteralPath $archive)) {
            Invoke-WebRequest -UseBasicParsing -Uri ($base + "$archiveName.zip") -OutFile $archive
        }
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
        if (-not $expected -or $actual -ne $expected.ToLower()) { throw "checksum mismatch for $archiveName.zip ($actual)" }
        $unpack = Join-Path $cache 'node-unpack'
        if (Test-Path -LiteralPath $unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force }
        Expand-Archive -LiteralPath $archive -DestinationPath $unpack -Force
        Copy-Item -LiteralPath (Join-Path $unpack $archiveName) -Destination $nodeDir -Recurse -Force
        Write-Host "Portable Node.js $nodeVersion ready: $nodeExe"
    } catch {
        Write-Warning ("Portable Node.js setup skipped: " + $_.Exception.Message)
    }
}
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npm) {
    Run-Native $npm.Source @('--prefix', '电力工具脚本/private-data-uploader-tool', 'ci', '--ignore-scripts', '--no-audit', '--no-fund')
} elseif (Test-Path -LiteralPath $nodeExe) {
    Run-Native $nodeExe @(
        (Join-Path $nodeDir 'node_modules/npm/bin/npm-cli.js'),
        '--prefix', '电力工具脚本/private-data-uploader-tool',
        'ci', '--ignore-scripts', '--no-audit', '--no-fund'
    )
} else {
    Write-Warning 'Private-data uploads are unavailable: Node.js 22+ was not found and could not be downloaded.'
}
Write-Host 'Windows environment ready. Run start_windows.ps1.'
