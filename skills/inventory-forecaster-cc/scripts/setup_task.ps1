# setup_task.ps1 - Run this ONCE (as Administrator) to register the scheduled task.
# After that, the forecaster runs automatically every 2 hours Mon-Fri, 6 AM to 8 PM.
#
# Usage:
#   Right-click PowerShell -> "Run as administrator"
#   cd to this scripts folder, then: .\setup_task.ps1

$TaskName  = "PP Inventory Forecaster"
$ScriptDir = "C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts"
$Launcher  = "$ScriptDir\run_scheduled.ps1"

# Detect pwsh (PS7) or fall back to Windows PowerShell 5
$_pwsh = Get-Command pwsh       -ErrorAction SilentlyContinue
$_ps5  = Get-Command powershell -ErrorAction SilentlyContinue
if     ($_pwsh) { $PwshPath = $_pwsh.Source }
elseif ($_ps5)  { $PwshPath = $_ps5.Source  }
else            { Write-Error "PowerShell not found. Aborting."; exit 1 }

Write-Host "PowerShell : $PwshPath"
Write-Host "Launcher   : $Launcher"

# -- Action -------------------------------------------------------------------
$Action = New-ScheduledTaskAction `
    -Execute          $PwshPath `
    -Argument         "-NonInteractive -ExecutionPolicy Bypass -File `"$Launcher`"" `
    -WorkingDirectory $ScriptDir

# -- Trigger: Mon-Fri at 6 AM, repeat every 2 h for 14 h (last run 8 PM) -----
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "6:00 AM"

# ISO 8601 duration strings: PT2H = 2 hours, PT14H = 14 hours
$Trigger.Repetition.Interval         = "PT2H"
$Trigger.Repetition.Duration         = "PT14H"
$Trigger.Repetition.StopAtDurationEnd = $false

# -- Settings -----------------------------------------------------------------
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances  IgnoreNew `
    -StartWhenAvailable `
    -WakeToRun

# -- Remove existing task if present ------------------------------------------
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Task '$TaskName' already exists - replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# -- Prompt for password (needed to run while screen is locked) ---------------
$User     = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Password = Read-Host "Enter your Windows login password (required to run when locked)" -AsSecureString
$PlainPw  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password))

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -RunLevel  Highest `
    -User      $User `
    -Password  $PlainPw `
    -Force

$PlainPw = $null

Write-Host ""
Write-Host "Done. Task '$TaskName' registered."
Write-Host "Schedule : Mon-Fri, every 2 hours from 6 AM to 8 PM (8 runs per day)"
Write-Host "Logs     : $ScriptDir\logs\"
Write-Host ""
Write-Host "To test right now:"
Write-Host "  Start-ScheduledTask -TaskName 'PP Inventory Forecaster'"
Write-Host "  Then check $ScriptDir\logs\ for the output file."
