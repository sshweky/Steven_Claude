# monday_profiles.ps1
# Runs build_category_profiles_from_report.py every Monday at 7:15am.
# Builds monthly seasonality profiles from the Invoices table.
#
# UPDATE BEFORE FIRST RUN:
#   $Python — full path to python.exe on this machine

$Python    = "C:\Python314\python.exe"
$SkillRoot = Split-Path -Parent $PSScriptRoot   # one level up from scripts\
$Script    = Join-Path $SkillRoot "build_category_profiles_from_report.py"
$LogDir    = Join-Path $PSScriptRoot "logs"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = "$LogDir\profiles_$Timestamp.log"
$Start     = [datetime]::Now

"[$($Start.ToString('yyyy-MM-dd HH:mm:ss'))] Starting build_category_profiles_from_report.py" |
    Out-File $LogFile -Encoding UTF8

# Run from skill root so relative imports (scripts/config.py etc.) resolve
Push-Location $SkillRoot
try {
    & $Python $Script 2>&1 | Out-File $LogFile -Append -Encoding UTF8
    $ExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Done. Exit=$ExitCode  Duration=$([math]::Round(([datetime]::Now-$Start).TotalSeconds))s" |
    Out-File $LogFile -Append -Encoding UTF8

# Keep 8 most recent logs (2 months of Mondays)
Get-ChildItem "$LogDir\profiles_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 8 |
    Remove-Item -Force
