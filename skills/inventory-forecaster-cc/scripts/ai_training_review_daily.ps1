# ai_training_review_daily.ps1
# Runs ai_training_review.py every day at 6:00am.
# Fetches unreviewed "AI Training" Projection Comments, deep-analyzes
# planner corrections vs the AI model, proposes rule changes, and
# emails a summary report.
#
# UPDATE BEFORE FIRST RUN:
#   $Python -- full path to python.exe on this machine

$Python  = "C:\Python314\python.exe"
$Script  = Join-Path $PSScriptRoot "ai_training_review.py"
$LogDir  = Join-Path $PSScriptRoot "logs"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = "$LogDir\ai_training_review_$Timestamp.log"
$Start     = [datetime]::Now

"[$($Start.ToString('yyyy-MM-dd HH:mm:ss'))] Starting ai_training_review.py" |
    Out-File $LogFile -Encoding UTF8

& $Python $Script 2>&1 | Out-File $LogFile -Append -Encoding UTF8
$ExitCode = $LASTEXITCODE

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Done. Exit=$ExitCode  Duration=$([math]::Round(([datetime]::Now-$Start).TotalSeconds))s" |
    Out-File $LogFile -Append -Encoding UTF8

# Keep 14 most recent logs (2 weeks)
Get-ChildItem "$LogDir\ai_training_review_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 14 |
    Remove-Item -Force
