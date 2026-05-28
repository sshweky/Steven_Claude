# run_scheduled.ps1 - launched by Windows Task Scheduler (PP Inventory Forecaster)
# Runs Mon-Fri at 6:00 AM. Captures all output, sends email summary via SMTP on completion.
# Lock file prevents overlapping runs if a previous instance is still active.

$Python    = "C:\Python314\python.exe"
$ScriptDir = 'C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts'
$LogDir    = "$ScriptDir\logs"
$LockFile  = "$ScriptDir\forecast.lock"
$MailTo    = "s.shweky@petspeople.com"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# ── Single-instance lock ──────────────────────────────────────────────────────
# If another instance is already running, skip this trigger entirely.
if (Test-Path $LockFile) {
    $LockAge = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($LockAge.TotalHours -lt 6) {
        # Lock is fresh -- another run is active. Exit silently.
        exit 0
    }
    # Lock is stale (>6 hours) -- previous run crashed without cleanup. Remove it.
    Remove-Item $LockFile -Force
}

# Create lock file
$PID | Out-File $LockFile -Encoding ASCII

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile   = "$LogDir\forecast_$Timestamp.log"
$StartTime = [datetime]::Now

"[$($StartTime.ToString('yyyy-MM-dd HH:mm:ss'))] Starting scheduled forecast run (--all --validate)" |
    Out-File $LogFile -Encoding UTF8

$ExitCode = 0
try {
    & $Python "$ScriptDir\run_forecast.py" "--all" "--validate" 2>&1 |
        Out-File $LogFile -Append -Encoding UTF8
    $ExitCode = $LASTEXITCODE
} catch {
    $ExitCode = 1
    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] ERROR: $_" |
        Out-File $LogFile -Append -Encoding UTF8
}

$EndTime  = [datetime]::Now
$Duration = [math]::Round(($EndTime - $StartTime).TotalMinutes, 1)

"[$($EndTime.ToString('yyyy-MM-dd HH:mm:ss'))] Finished. Exit code: $ExitCode  Duration: ${Duration}m" |
    Out-File $LogFile -Append -Encoding UTF8

# Release lock
Remove-Item $LockFile -ErrorAction SilentlyContinue

# Rotate: keep only the 20 most recent log files
Get-ChildItem "$LogDir\forecast_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Force

# ── Parse log for run stats ───────────────────────────────────────────────────
$LogText = Get-Content $LogFile -Raw -Encoding UTF8

$RecordsMatch   = [regex]::Match($LogText, '(\d[\d,]+)\s+records?\s+retrieved')
$Records        = if ($RecordsMatch.Success)   { $RecordsMatch.Groups[1].Value   } else { "?" }

$FcstMatch      = [regex]::Match($LogText, '(\d+)\s+forecasts? complete')
$Forecasts      = if ($FcstMatch.Success)      { $FcstMatch.Groups[1].Value      } else { "?" }

$SeasonalMatch  = [regex]::Match($LogText, 'Seasonal:\s*(\d+)')
$CrostonsMatch  = [regex]::Match($LogText, "Croston.s:\s*(\d+)")
$HeuristicMatch = [regex]::Match($LogText, 'Heuristic:\s*(\d+)')
$InactiveMatch  = [regex]::Match($LogText, 'Inactive:\s*(\d+)')
$BiweeklyMatch  = [regex]::Match($LogText, 'Bi-weekly:\s*(\d+)')
$ModelSeasonal  = if ($SeasonalMatch.Success)  { $SeasonalMatch.Groups[1].Value  } else { "-" }
$ModelCrostons  = if ($CrostonsMatch.Success)  { $CrostonsMatch.Groups[1].Value  } else { "-" }
$ModelHeuristic = if ($HeuristicMatch.Success) { $HeuristicMatch.Groups[1].Value } else { "-" }
$ModelInactive  = if ($InactiveMatch.Success)  { $InactiveMatch.Groups[1].Value  } else { "-" }
$ModelBiweekly  = if ($BiweeklyMatch.Success)  { $BiweeklyMatch.Groups[1].Value  } else { "-" }

$DemandMatch    = [regex]::Match($LogText, 'Total 26w demand:\s*([\d,]+)')
$Demand         = if ($DemandMatch.Success)    { $DemandMatch.Groups[1].Value    } else { "?" }

$WbMatch        = [regex]::Match($LogText, 'ok=(\d+)\s+fail=(\d+)')
$WbOk           = if ($WbMatch.Success)        { $WbMatch.Groups[1].Value        } else { "?" }
$WbFail         = if ($WbMatch.Success)        { $WbMatch.Groups[2].Value        } else { "?" }

$AlertMatch     = [regex]::Match($LogText, 'ALERTS\s+\((\d+)\s+records')
$AlertCount     = if ($AlertMatch.Success)     { $AlertMatch.Groups[1].Value     } else { "0" }

$ErrorLines     = ($LogText -split "`n" | Where-Object { $_ -match 'ERROR|Traceback|NameError|Exception' }) -join "`n"
$HasErrors      = $ErrorLines.Length -gt 0

# ── Build email ───────────────────────────────────────────────────────────────
$Status      = if ($ExitCode -eq 0 -and -not $HasErrors) { "OK" } else { "CHECK ERRORS" }
$StatusColor = if ($Status -eq "OK") { "#2e7d32" } else { "#c62828" }
$Subject     = "[PP Forecast $Status] $($StartTime.ToString('ddd MM/dd')) - ${Duration}m"

$LogLines   = $LogText -split "`n"
$TruncNote  = ""
if ($LogLines.Count -gt 200) {
    $LogPreview = ($LogLines | Select-Object -Last 200) -join "`n"
    $TruncNote  = "<p style='color:#888;font-size:12px'>[Log truncated to last 200 lines. Full log: $LogFile]</p>"
} else {
    $LogPreview = $LogText
}

$AlertRow = if ([int]$AlertCount -gt 0) {
    "<tr><td style='padding:4px 12px;color:#888'>Alerts</td><td style='padding:4px 12px;color:#e65100;font-weight:bold'>$AlertCount records flagged</td></tr>"
} else { "" }

$ErrorRow = if ($HasErrors) {
    "<tr><td style='padding:4px 12px;color:#888'>Errors</td><td style='padding:4px 12px;color:#c62828;font-weight:bold'>YES - see log below</td></tr>"
} else { "" }

Add-Type -AssemblyName System.Web | Out-Null
$LogEncoded = [System.Web.HttpUtility]::HtmlEncode($LogPreview)

$HtmlBody = @"
<div style="font-family:Calibri,Arial,sans-serif;font-size:14px;max-width:800px">
  <h2 style="margin-bottom:4px;color:$StatusColor">PP Inventory Forecaster - $Status</h2>
  <p style="color:#555;margin-top:0">Run completed $($EndTime.ToString('dddd, MMMM d yyyy')) at $($EndTime.ToString('h:mm tt'))</p>
  <table style="border-collapse:collapse;background:#f9f9f9;border-radius:6px;margin-bottom:20px">
    <tr><td style="padding:4px 12px;color:#888">Start</td><td style="padding:4px 12px">$($StartTime.ToString('h:mm tt'))</td></tr>
    <tr><td style="padding:4px 12px;color:#888">End</td><td style="padding:4px 12px">$($EndTime.ToString('h:mm tt'))</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Duration</td><td style="padding:4px 12px">${Duration} min</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Exit Code</td><td style="padding:4px 12px">$ExitCode</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Records Pulled</td><td style="padding:4px 12px">$Records</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Forecasts Run</td><td style="padding:4px 12px">$Forecasts</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Total 26w Demand</td><td style="padding:4px 12px">$Demand units</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Write-back</td><td style="padding:4px 12px">ok=$WbOk  fail=$WbFail</td></tr>
    $AlertRow
    $ErrorRow
  </table>
  <h3 style="color:#333;margin-bottom:4px">Model Breakdown</h3>
  <table style="border-collapse:collapse;background:#f9f9f9;border-radius:6px;margin-bottom:20px">
    <tr><td style="padding:4px 12px;color:#888">Seasonal Baseline</td><td style="padding:4px 12px">$ModelSeasonal</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Croston's</td><td style="padding:4px 12px">$ModelCrostons</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Heuristic</td><td style="padding:4px 12px">$ModelHeuristic</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Inactive</td><td style="padding:4px 12px">$ModelInactive</td></tr>
    <tr><td style="padding:4px 12px;color:#888">Bi-weekly</td><td style="padding:4px 12px">$ModelBiweekly</td></tr>
  </table>
  <h3 style="color:#333;margin-bottom:4px">Run Log</h3>
  $TruncNote
  <pre style="background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:6px;font-size:12px;overflow-x:auto;white-space:pre-wrap">$LogEncoded</pre>
</div>
"@

# ── Send via Python SMTP (no Outlook required) ────────────────────────────────
try {
    $TempHtml = "$LogDir\forecast_email_$Timestamp.html"
    [System.IO.File]::WriteAllText($TempHtml, $HtmlBody, [System.Text.Encoding]::UTF8)

    $SendResult = & $Python "$ScriptDir\send_email.py" `
        --to $MailTo `
        --subject $Subject `
        --body-file $TempHtml `
        --html 2>&1

    Remove-Item $TempHtml -ErrorAction SilentlyContinue

    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Email result: $SendResult" |
        Out-File $LogFile -Append -Encoding UTF8
} catch {
    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Email send failed: $_" |
        Out-File $LogFile -Append -Encoding UTF8
}
