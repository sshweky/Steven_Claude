# run_scheduled.ps1 - launched by Windows Task Scheduler every 2 hours Mon-Fri
# Runs the inventory forecaster and logs all output with a timestamped log file.
# Keeps the 20 most recent log files; older ones are deleted automatically.

$Python    = "C:\Python314\python.exe"
$ScriptDir = "C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts"
$LogDir    = "$ScriptDir\logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = "$LogDir\forecast_$Timestamp.log"

"[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Starting scheduled forecast run (--all)" |
    Out-File $LogFile -Encoding UTF8

try {
    & $Python "$ScriptDir\run_forecast.py" "--all" 2>&1 |
        Out-File $LogFile -Append -Encoding UTF8
    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Finished. Exit code: $LASTEXITCODE" |
        Out-File $LogFile -Append -Encoding UTF8
} catch {
    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] ERROR: $_" |
        Out-File $LogFile -Append -Encoding UTF8
}

# Rotate: keep only the 20 most recent log files
Get-ChildItem "$LogDir\forecast_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Force
