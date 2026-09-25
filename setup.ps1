# Dispodex one-time setup (safe to run again).
# Right-click this file → "Run with PowerShell", or run:  powershell -ExecutionPolicy Bypass -File setup.ps1
#
#   1. Creates the Python environment (.venv) and installs packages
#   2. Creates .env from .env.example (with a secret key)
#   3. Builds the database
#   4. Optionally copies everything from the old PHP Pinksheet
param(
    [string]$ImportFrom = "",
    [switch]$Dev
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "Dispodex setup" -ForegroundColor Cyan

if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = "py"; $pyArgs = @("-3") } else { $pyExe = "python"; $pyArgs = @() }
$version = & $pyExe @pyArgs -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$version -lt [version]"3.10") {
    throw "Python 3.10 or newer is required (found $version). Install it from https://www.python.org/downloads/"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating the Python environment..."
    & $pyExe @pyArgs -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --quiet --upgrade pip
if ($Dev) { & $venvPy -m pip install --quiet -r requirements-dev.txt } else { & $venvPy -m pip install --quiet -r requirements.txt }

if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example..."
    Copy-Item ".env.example" ".env"
}

Write-Host "Building the database..."
& $venvPy manage.py migrate --noinput
& $venvPy manage.py collectstatic --noinput | Out-Null

if ($ImportFrom -eq "") {
    $default = Join-Path ([Environment]::GetFolderPath("Desktop")) "pinksheet"
    if (Test-Path (Join-Path $default "data\intake.sqlite")) {
        $answer = Read-Host "Copy the data from the old Pinksheet at $default ? (y/n)"
        if ($answer -match '^[Yy]') { $ImportFrom = $default }
    }
}
if ($ImportFrom -ne "") {
    & $venvPy manage.py import_legacy "$ImportFrom"
}

Write-Host ""
Write-Host "Done. Double-click start.bat to run Dispodex." -ForegroundColor Green
Write-Host "To connect Square later:  .venv\Scripts\python.exe manage.py configure_square"
