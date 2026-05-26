# daily_alert_dismiss.ps1
# Runs dismiss_reviewed_alerts.py every day at 7:20am.
# Clears AI_ALERT on projections where the latest flag comment is Reviewed or FYI.
#
# UPDATE BEFORE FIRST RUN:
#   $Python — full path to python.exe on this machine

$Python  = "C:\Python314\python.exe"
$Script  = Join-Path $PSScriptRoot "dismiss_reviewed_alerts.py"
$LogDir  = Join-Path $PSScriptRoot "logs"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = "$LogDir\alert_dismiss_$Timestamp.log"
$Start     = [datetime]::Now

"[$($Start.ToString('yyyy-MM-dd HH:mm:ss'))] Starting dismiss_reviewed_alerts.py" |
    Out-File $LogFile -Encoding UTF8

& $Python $Script 2>&1 | Out-File $LogFile -Append -Encoding UTF8
$ExitCode = $LASTEXITCODE

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Done. Exit=$ExitCode  Duration=$([math]::Round(([datetime]::Now-$Start).TotalSeconds))s" |
    Out-File $LogFile -Append -Encoding UTF8

# Keep 14 most recent logs (2 weeks)
Get-ChildItem "$LogDir\alert_dismiss_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 |
    Remove-Item -Force
