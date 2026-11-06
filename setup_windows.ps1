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
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npm) {
    Run-Native $npm.Source @('--prefix', '电力工具脚本/private-data-uploader-tool', 'ci', '--ignore-scripts', '--no-audit', '--no-fund')
} else {
    Write-Warning 'Node.js 22+ is needed only for private-data uploads and desktop builds.'
}
Write-Host 'Windows environment ready. Run start_windows.ps1.'
