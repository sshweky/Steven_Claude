# setup_task.ps1 - Run this ONCE (as Administrator) to register the scheduled task.
# Creates a wrapper batch file at a simple path (no special chars) and registers
# a Task Scheduler job pointing to it.
#
# Usage:
#   Right-click PowerShell -> "Run as administrator"
#   cd to this scripts folder, then: .\setup_task.ps1

$TaskName   = "PP Inventory Forecaster"
$PS1Script  = 'C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts\run_scheduled.ps1'
$WrapperDir = "C:\ProgramData\PPForecast"
$WrapperBat = "$WrapperDir\run.bat"
$Python     = "C:\Python314\python.exe"
$RunForecast = 'C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts\run_forecast.py'
$LogDir     = 'C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts\logs'

# -- Create wrapper directory and batch file ----------------------------------
if (-not (Test-Path $WrapperDir)) {
    New-Item -ItemType Directory -Path $WrapperDir | Out-Null
    Write-Host "Created $WrapperDir"
}

# The batch file lives at a path with no special characters.
# Inside the batch file the path is quoted, so & and ( are handled fine.
# Set PYTHONPATH so the SYSTEM account finds the right site-packages
$BatContent = "@echo off`r`nset PYTHONPATH=C:\Python314\Lib\site-packages`r`nset PYTHONHOME=C:\Python314`r`npowershell.exe -NonInteractive -ExecutionPolicy Bypass -File `"$PS1Script`"`r`n"
[System.IO.File]::WriteAllText($WrapperBat, $BatContent, [System.Text.Encoding]::ASCII)
Write-Host "Wrapper : $WrapperBat"

# -- Remove existing task if present ------------------------------------------
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Task '$TaskName' already exists - replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# -- Register via schtasks.exe (SYSTEM account - no password needed) -----------
# PYTHONPATH is set in run.bat so SYSTEM finds the right site-packages.
# /sc WEEKLY /d MON-FRI /st 06:00 : starts 6 AM on weekdays
# /ri 120 : repeat every 120 minutes (2 hours)
# /du 0014:00 : for 14 hours (last run 8 PM)
# /rl HIGHEST : run with highest available privileges
$result = & schtasks /create `
    /tn $TaskName `
    /tr "`"$WrapperBat`"" `
    /sc WEEKLY `
    /d  MON,TUE,WED,THU,FRI `
    /st 06:00 `
    /ri 120 `
    /du 0014:00 `
    /rl HIGHEST `
    /ru SYSTEM `
    /f 2>&1

Write-Host $result

Write-Host ""
Write-Host "Done. Task '$TaskName' registered."
Write-Host "Schedule : Mon-Fri every 2 hours, 6 AM to 8 PM"
Write-Host "Wrapper  : $WrapperBat"
Write-Host "Logs     : $LogDir\"
Write-Host ""
Write-Host "To test right now (run as admin):"
Write-Host "  schtasks /run /tn `"$TaskName`""
Write-Host "  Then check $LogDir\ for the output file."
