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

$RecordsMatch    = [regex]::Match($LogText, '(\d[\d,]+)\s+records?\s+retrieved')
$Records         = if ($RecordsMatch.Success)    { $RecordsMatch.Groups[1].Value    } else { "?" }

$FcstMatch       = [regex]::Match($LogText, '(\d+)\s+forecasts? complete')
$Forecasts       = if ($FcstMatch.Success)       { $FcstMatch.Groups[1].Value       } else { "?" }

$DemandMatch     = [regex]::Match($LogText, 'Total 26w demand:\s*([\d,]+)')
$Demand          = if ($DemandMatch.Success)     { $DemandMatch.Groups[1].Value     } else { "?" }

$WbMatch         = [regex]::Match($LogText, 'ok=(\d+)\s+fail=(\d+)')
$WbOk            = if ($WbMatch.Success)         { $WbMatch.Groups[1].Value         } else { "?" }
$WbFail          = if ($WbMatch.Success)         { $WbMatch.Groups[2].Value         } else { "?" }

# MAN vs AI divergence stats (printed by _print_summary in inventory_forecaster.py)
$ManTotalMatch   = [regex]::Match($LogText, 'MAN 26w:\s*([\d,]+)')
$AiTotalMatch    = [regex]::Match($LogText, 'AI 26w:\s*([\d,]+)')
$OverallDivMatch = [regex]::Match($LogText, 'Overall divergence:\s*([+-]?[\d.]+)%')
$AvgDivMatch     = [regex]::Match($LogText, 'Avg \|div\|:\s*([\d.]+)%')
$ManTotal        = if ($ManTotalMatch.Success)   { $ManTotalMatch.Groups[1].Value   } else { "?" }
$AiTotal         = if ($AiTotalMatch.Success)    { $AiTotalMatch.Groups[1].Value    } else { "?" }
$OverallDiv      = if ($OverallDivMatch.Success) { $OverallDivMatch.Groups[1].Value } else { "?" }
$AvgDiv          = if ($AvgDivMatch.Success)     { $AvgDivMatch.Groups[1].Value     } else { "?" }

# Priority breakdown (On-Plan / CRITICAL / HIGH / MID / LOW)
$PriLineMatch    = [regex]::Match($LogText, 'Priority -- On-Plan:\s*(\d+)\s+CRITICAL:\s*(\d+)\s+HIGH:\s*(\d+)\s+MID:\s*(\d+)\s+LOW:\s*(\d+)')
$PriOnPlan       = if ($PriLineMatch.Success) { $PriLineMatch.Groups[1].Value } else { "?" }
$PriCrit         = if ($PriLineMatch.Success) { $PriLineMatch.Groups[2].Value } else { "?" }
$PriHigh         = if ($PriLineMatch.Success) { $PriLineMatch.Groups[3].Value } else { "?" }
$PriMid          = if ($PriLineMatch.Success) { $PriLineMatch.Groups[4].Value } else { "?" }
$PriLow          = if ($PriLineMatch.Success) { $PriLineMatch.Groups[5].Value } else { "?" }

# Model mix
$SeasonalMatch   = [regex]::Match($LogText, 'Seasonal:\s*(\d+)')
$CrostonsMatch   = [regex]::Match($LogText, "Croston.s:\s*(\d+)")
$HeuristicMatch  = [regex]::Match($LogText, 'Heuristic:\s*(\d+)')
$InactiveMatch   = [regex]::Match($LogText, 'Inactive:\s*(\d+)')
$BiweeklyMatch   = [regex]::Match($LogText, 'Bi-weekly:\s*(\d+)')
$ModelSeasonal   = if ($SeasonalMatch.Success)  { $SeasonalMatch.Groups[1].Value  } else { "-" }
$ModelCrostons   = if ($CrostonsMatch.Success)  { $CrostonsMatch.Groups[1].Value  } else { "-" }
$ModelHeuristic  = if ($HeuristicMatch.Success) { $HeuristicMatch.Groups[1].Value } else { "-" }
$ModelInactive   = if ($InactiveMatch.Success)  { $InactiveMatch.Groups[1].Value  } else { "-" }
$ModelBiweekly   = if ($BiweeklyMatch.Success)  { $BiweeklyMatch.Groups[1].Value  } else { "-" }

$AlertMatch      = [regex]::Match($LogText, 'ALERTS\s+\((\d+)\s+records')
$AlertCount      = if ($AlertMatch.Success) { $AlertMatch.Groups[1].Value } else { "0" }

$ErrorLines      = ($LogText -split "`n" | Where-Object { $_ -match '\bERROR\b|Traceback|NameError|AttributeError' }) -join "`n"
$HasErrors       = $ErrorLines.Length -gt 0

# ── Build email ───────────────────────────────────────────────────────────────
$IsOk        = ($ExitCode -eq 0 -and -not $HasErrors)
$Status      = if ($IsOk) { "Complete" } else { "Action Required" }
$StatusColor = if ($IsOk) { "#1b5e20" } else { "#b71c1c" }
$StatusBg    = if ($IsOk) { "#e8f5e9" } else { "#ffebee" }
$StatusBdr   = if ($IsOk) { "#66bb6a" } else { "#ef9a9a" }
$Subject     = if ($IsOk) {
    "[PP Forecast] $($StartTime.ToString('ddd MM/dd')) -- Complete in ${Duration}m"
} else {
    "[PP Forecast] $($StartTime.ToString('ddd MM/dd')) -- ACTION REQUIRED"
}

# Divergence hero stat: color based on direction
$DivDisplay  = if ($OverallDiv -ne "?") { "${OverallDiv}%" } else { "N/A" }
$DivNum      = if ($OverallDiv -ne "?") { [double]$OverallDiv } else { 0 }
$DivColor    = if ($DivNum -gt 5) { "#c62828" } elseif ($DivNum -lt -5) { "#1565c0" } else { "#2e7d32" }
$DivLabel    = if ($DivNum -gt 0) { "AI is projecting MORE than plan" } `
               elseif ($DivNum -lt 0) { "AI is projecting LESS than plan" } `
               else { "AI and plan are aligned" }

# Priority pill rendering
function Pill($val, $color, $bg) {
    "<span style='display:inline-block;padding:2px 10px;border-radius:12px;background:$bg;color:$color;font-weight:700;font-size:13px;margin:0 3px'>$val</span>"
}
$PillCrit    = Pill "CRITICAL $PriCrit"  "#b71c1c" "#ffebee"
$PillHigh    = Pill "HIGH $PriHigh"      "#e65100" "#fff3e0"
$PillMid     = Pill "MID $PriMid"        "#f57f17" "#fffde7"
$PillLow     = Pill "LOW $PriLow"        "#2e7d32" "#e8f5e9"
$PillOnPlan  = Pill "ON-PLAN $PriOnPlan" "#37474f" "#eceff1"

$ErrorBlock  = if ($HasErrors) { @"
<div style="margin:16px 0;padding:12px 16px;background:#ffebee;border-left:4px solid #c62828;border-radius:4px">
  <b style="color:#c62828">Errors detected in run log</b><br>
  <pre style="font-size:11px;color:#555;margin:8px 0 0;white-space:pre-wrap">$([System.Web.HttpUtility]::HtmlEncode($ErrorLines))</pre>
</div>
"@ } else { "" }

Add-Type -AssemblyName System.Web | Out-Null

$HtmlBody = @"
<div style="font-family:'Segoe UI',Calibri,Arial,sans-serif;font-size:14px;color:#212121;max-width:680px;margin:0 auto">

  <!-- Header bar -->
  <div style="background:$StatusBg;border:1px solid $StatusBdr;border-radius:8px;padding:16px 20px;margin-bottom:20px">
    <div style="display:flex;align-items:center;gap:12px">
      <span style="font-size:22px;font-weight:700;color:$StatusColor">PP Inventory Forecaster</span>
      <span style="font-size:13px;color:#555;margin-left:auto">$($EndTime.ToString('dddd, MMMM d yyyy'))  &bull;  $($EndTime.ToString('h:mm tt'))  &bull;  ${Duration} min</span>
    </div>
    <div style="margin-top:4px;font-size:13px;color:$StatusColor;font-weight:600">$Status</div>
  </div>

  <!-- HERO: MAN vs AI Divergence -->
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:20px 24px;margin-bottom:16px;text-align:center">
    <div style="font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#757575;margin-bottom:6px">Overall MAN vs AI Divergence (26-Week)</div>
    <div style="font-size:56px;font-weight:800;color:$DivColor;line-height:1.1;letter-spacing:-1px">${DivDisplay}</div>
    <div style="font-size:13px;color:#555;margin-top:6px">$DivLabel &nbsp;&bull;&nbsp; Avg per-record gap: ${AvgDiv}%</div>
    <div style="display:flex;justify-content:center;gap:32px;margin-top:16px;padding-top:16px;border-top:1px solid #f0f0f0">
      <div><div style="font-size:11px;color:#9e9e9e;text-transform:uppercase;letter-spacing:.06em">Manual Plan</div><div style="font-size:22px;font-weight:700;color:#37474f">$ManTotal</div><div style="font-size:11px;color:#9e9e9e">units (26w)</div></div>
      <div style="border-left:1px solid #e0e0e0"></div>
      <div><div style="font-size:11px;color:#9e9e9e;text-transform:uppercase;letter-spacing:.06em">AI Forecast</div><div style="font-size:22px;font-weight:700;color:#37474f">$AiTotal</div><div style="font-size:11px;color:#9e9e9e">units (26w)</div></div>
      <div style="border-left:1px solid #e0e0e0"></div>
      <div><div style="font-size:11px;color:#9e9e9e;text-transform:uppercase;letter-spacing:.06em">Total AI Demand</div><div style="font-size:22px;font-weight:700;color:#37474f">$Demand</div><div style="font-size:11px;color:#9e9e9e">units (all records)</div></div>
    </div>
  </div>

  <!-- Review Priority -->
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:16px 20px;margin-bottom:16px">
    <div style="font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#757575;margin-bottom:10px">Review Priority</div>
    <div style="line-height:2">$PillCrit $PillHigh $PillMid $PillLow $PillOnPlan</div>
  </div>

  <!-- Run Summary grid -->
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:16px 20px;margin-bottom:16px">
    <div style="font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#757575;margin-bottom:12px">Run Summary</div>
    <table style="border-collapse:collapse;width:100%">
      <tr>
        <td style="padding:5px 8px;color:#757575;width:38%">Records Processed</td>
        <td style="padding:5px 8px;font-weight:600">$Forecasts of $Records</td>
        <td style="padding:5px 8px;color:#757575;width:20%">Write-back</td>
        <td style="padding:5px 8px;font-weight:600">${WbOk} ok &nbsp; ${WbFail} fail</td>
      </tr>
      <tr style="background:#fafafa">
        <td style="padding:5px 8px;color:#757575">Alerts (&gt;threshold)</td>
        <td style="padding:5px 8px;font-weight:600;color:$(if ([int]$AlertCount -gt 0) {'#e65100'} else {'#2e7d32'})">$AlertCount records</td>
        <td style="padding:5px 8px;color:#757575">Duration</td>
        <td style="padding:5px 8px;font-weight:600">${Duration} min</td>
      </tr>
    </table>
  </div>

  <!-- Model Mix -->
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:16px 20px;margin-bottom:16px">
    <div style="font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#757575;margin-bottom:12px">Model Mix</div>
    <table style="border-collapse:collapse;width:100%">
      <tr>
        <td style="padding:5px 8px;color:#757575">Seasonal Baseline</td><td style="padding:5px 8px;font-weight:600">$ModelSeasonal</td>
        <td style="padding:5px 8px;color:#757575">Croston's</td><td style="padding:5px 8px;font-weight:600">$ModelCrostons</td>
      </tr>
      <tr style="background:#fafafa">
        <td style="padding:5px 8px;color:#757575">Heuristic</td><td style="padding:5px 8px;font-weight:600">$ModelHeuristic</td>
        <td style="padding:5px 8px;color:#757575">Inactive</td><td style="padding:5px 8px;font-weight:600">$ModelInactive</td>
      </tr>
      <tr>
        <td style="padding:5px 8px;color:#757575">Bi-weekly</td><td style="padding:5px 8px;font-weight:600">$ModelBiweekly</td>
        <td colspan="2"></td>
      </tr>
    </table>
  </div>

  $ErrorBlock

  <p style="font-size:11px;color:#9e9e9e;margin-top:20px">Log: $LogFile</p>
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
