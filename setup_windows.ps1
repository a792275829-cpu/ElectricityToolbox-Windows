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
$expectedPython = (Get-Content -LiteralPath (Join-Path $root '.python-version') -Raw).Trim()
if ($expectedPython -notmatch '^3\.\d+\.\d+$') { throw 'Invalid .python-version; expected an exact Python version.' }
$python = Join-Path $root '.venv/Scripts/python.exe'
function Assert-PinnedPython([string]$Program) {
    $actual = & $Program -c "import sys,struct,tkinter;print('.'.join(map(str,sys.version_info[:3])));sys.exit(0 if struct.calcsize('P')==8 else 1)"
    if ($LASTEXITCODE -ne 0 -or "$actual".Trim() -ne $expectedPython) {
        throw "Python $expectedPython x64 with Tk is required by .python-version. Install it or use the pinned standalone runtime; the existing .venv was not replaced."
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    $runtimeRoot = Join-Path $root '.toolbox/standalone-python'
    $candidate = Join-Path $runtimeRoot 'python.exe'
    if (-not (Test-Path -LiteralPath $candidate)) {
        $candidate = Get-ChildItem -LiteralPath $runtimeRoot -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $candidate) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $minor = ($expectedPython -split '\.')[0..1] -join '.'
            $candidate = & py "-$minor" -c 'import sys;print(sys.executable)'
            if ($LASTEXITCODE -ne 0) { throw "Install Python $expectedPython x64, then rerun setup_windows.ps1." }
        } else {
            $candidate = (Get-Command python -ErrorAction Stop).Source
        }
    }
    Assert-PinnedPython $candidate
    Run-Native $candidate @('-m', 'venv', (Join-Path $root '.venv'))
}
Assert-PinnedPython $python
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
try {
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
} catch {
    Write-Warning ("Private-data uploader initialization failed: " + $_.Exception.Message)
}
Write-Host 'Windows Python environment ready. Run start_windows.ps1.'
