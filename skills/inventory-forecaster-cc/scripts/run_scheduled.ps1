# run_scheduled.ps1 - launched by Windows Task Scheduler every 2 hours Mon-Fri
# Runs the inventory forecaster and logs all output with a timestamped log file.
# Keeps the 20 most recent log files; older ones are deleted automatically.
# Sends the log by email on completion if C:\ProgramData\PPForecast\mail_config.txt exists.

$Python    = "C:\Python314\python.exe"
$ScriptDir = 'C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts'
$LogDir    = "$ScriptDir\logs"
$MailCfg   = "C:\ProgramData\PPForecast\mail_config.txt"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

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
    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Finished. Exit code: $ExitCode" |
        Out-File $LogFile -Append -Encoding UTF8
} catch {
    $ExitCode = 1
    "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] ERROR: $_" |
        Out-File $LogFile -Append -Encoding UTF8
}

# Rotate: keep only the 20 most recent log files
Get-ChildItem "$LogDir\forecast_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Force

# Send email via Microsoft Graph API if mail_config.txt exists.
# Required keys: GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, MAIL_FROM, MAIL_TO
# One-time setup: register an Azure AD app with Mail.Send application permission.
if (Test-Path $MailCfg) {
    try {
        $cfg = @{}
        Get-Content $MailCfg | ForEach-Object {
            if ($_ -match '^(\w+)=(.+)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
        }

        $Duration = [math]::Round(([datetime]::Now - $StartTime).TotalMinutes, 1)
        $Status   = if ($ExitCode -eq 0) { "OK" } else { "FAILED (exit $ExitCode)" }
        $Subject  = "[$Status] PP Forecast run $($StartTime.ToString('MM/dd HH:mm'))"
        $LogText  = Get-Content $LogFile -Raw -Encoding UTF8

        # Step 1 — Get OAuth2 access token via client credentials flow
        $TokenUrl  = "https://login.microsoftonline.com/$($cfg['GRAPH_TENANT_ID'])/oauth2/v2.0/token"
        $TokenBody = @{
            grant_type    = "client_credentials"
            client_id     = $cfg['GRAPH_CLIENT_ID']
            client_secret = $cfg['GRAPH_CLIENT_SECRET']
            scope         = "https://graph.microsoft.com/.default"
        }
        $TokenResp   = Invoke-RestMethod -Method Post -Uri $TokenUrl -Body $TokenBody
        $AccessToken = $TokenResp.access_token

        # Step 2 — Send via Graph sendMail endpoint
        # Truncate log body to 1 MB to stay within Graph message size limits
        $MaxBody  = 1048576
        $BodyText = "Run completed in $Duration min.`n`n" +
                    $(if ($LogText.Length -gt $MaxBody) { $LogText.Substring(0, $MaxBody) + "`n...[truncated]" } else { $LogText })

        $MailPayload = @{
            message = @{
                subject = $Subject
                body    = @{ contentType = "Text"; content = $BodyText }
                toRecipients = @( @{ emailAddress = @{ address = $cfg['MAIL_TO'] } } )
            }
        } | ConvertTo-Json -Depth 6

        $GraphUrl = "https://graph.microsoft.com/v1.0/users/$($cfg['MAIL_FROM'])/sendMail"
        Invoke-RestMethod -Method Post -Uri $GraphUrl -Body $MailPayload `
            -Headers @{ Authorization = "Bearer $AccessToken"; "Content-Type" = "application/json" }

        "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Email sent to $($cfg['MAIL_TO'])" |
            Out-File $LogFile -Append -Encoding UTF8
    } catch {
        "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Email send failed: $_" |
            Out-File $LogFile -Append -Encoding UTF8
    }
}
