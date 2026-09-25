# Start Dispodex automatically when this computer starts (Windows Task Scheduler).
# Run once in an elevated PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\install_startup_task.ps1
# Remove it again with:                 Unregister-ScheduledTask -TaskName "Dispodex" -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run setup.ps1 first." }

$action = New-ScheduledTaskAction -Execute $python -Argument "manage.py serve" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

# The app used to be called Pinksheet: replace a task installed under the old name so it doesn't start twice.
if (Get-ScheduledTask -TaskName "Pinksheet" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "Pinksheet" -Confirm:$false
    Write-Host "Removed the old 'Pinksheet' startup task."
}

Register-ScheduledTask -TaskName "Dispodex" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Dispodex inventory server" -Force | Out-Null
Write-Host "Dispodex will now start automatically with Windows." -ForegroundColor Green
Write-Host "Start it right away with:  Start-ScheduledTask -TaskName Dispodex"
