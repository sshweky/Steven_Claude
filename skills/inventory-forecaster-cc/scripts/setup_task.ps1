# setup_task.ps1 - Run this ONCE (as Administrator) to register the scheduled task.
# After that, the forecaster runs every 2 hours Mon-Fri, 6 AM to 8 PM.
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

# Task XML - repetition must be set here; PowerShell CIM objects don't expose it
# Runs Mon-Fri at 6 AM, repeats every 2 hours, stops after 14 hours (last run 8 PM)
$TaskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Pets+People inventory forecaster - runs every 2 hours Mon-Fri</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT2H</Interval>
        <Duration>PT14H</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-05-25T06:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <Enabled>true</Enabled>
  </Settings>
  <Actions>
    <Exec>
      <Command>$PwshPath</Command>
      <Arguments>-NonInteractive -ExecutionPolicy Bypass -File "$Launcher"</Arguments>
      <WorkingDirectory>$ScriptDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Remove existing task if present
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Task '$TaskName' already exists - replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Register - password needed to run while screen is locked
$User     = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Password = Read-Host "Enter your Windows login password (required to run when locked)" -AsSecureString
$PlainPw  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password))

Register-ScheduledTask `
    -TaskName $TaskName `
    -Xml      $TaskXml `
    -User     $User `
    -Password $PlainPw `
    -Force

$PlainPw = $null

Write-Host ""
Write-Host "Done. Task '$TaskName' registered."
Write-Host "Schedule : Mon-Fri, every 2 hours from 6 AM to 8 PM"
Write-Host "Logs     : $ScriptDir\logs\"
Write-Host ""
Write-Host "To test right now:"
Write-Host "  Start-ScheduledTask -TaskName 'PP Inventory Forecaster'"
Write-Host "  Then check $ScriptDir\logs\ for the output file."
