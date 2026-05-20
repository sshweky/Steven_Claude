# run_scheduled.ps1 - launched by Windows Task Scheduler every 2 hours Mon-Fri
# Runs the inventory forecaster and logs all output with a timestamped log file.
# Keeps the 20 most recent log files; older ones are deleted automatically.
# Sends the log by email on completion if C:\ProgramData\PPForecast\mail_config.txt exists.

$Python    = "C:\Python314\python.exe"
$ScriptDir = "C:\Users\StevenShweky(Fetch&B\.claude\skills\inventory-forecaster-cc\scripts"
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

# Send email if mail_config.txt exists
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

        $SmtpClient = New-Object System.Net.Mail.SmtpClient($cfg['SMTP_SERVER'], 587)
        $SmtpClient.EnableSsl   = $true
        $SmtpClient.Credentials = New-Object System.Net.NetworkCredential($cfg['SMTP_USER'], $cfg['SMTP_PASS'])

        $Msg             = New-Object System.Net.Mail.MailMessage
        $Msg.From        = $cfg['SMTP_FROM']
        $Msg.To.Add($cfg['SMTP_TO'])
        $Msg.Subject     = $Subject
        $Msg.Body        = "Run completed in $Duration min.`n`n$LogText"
        $Msg.IsBodyHtml  = $false

        $SmtpClient.Send($Msg)
        $Msg.Dispose()
    } catch {
        "[$([datetime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] Email send failed: $_" |
            Out-File $LogFile -Append -Encoding UTF8
    }
}
