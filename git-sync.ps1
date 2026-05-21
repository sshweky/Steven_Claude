$claude = "$env:USERPROFILE\.claude"
$logFile = "$claude\git-sync.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Out-File -Append -Encoding utf8 -FilePath $logFile
}

Set-Location $claude

# Stage any uncommitted changes
$status = git -C $claude status --porcelain 2>&1
if ($status) {
    git -C $claude add -A 2>&1 | Out-Null
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $commitOut = git -C $claude commit -m "Auto-sync $timestamp" 2>&1
    Log "Committed: $($commitOut -join ' ')"
} else {
    Log "No local changes to commit"
}

# Pull remote changes (rebase) then push
$pullOut = git -C $claude pull --rebase origin master 2>&1
Log "Pull: $($pullOut -join ' | ')"

$pushOut = git -C $claude push origin master 2>&1
Log "Push: $($pushOut -join ' | ')"

# Trim log to last 500 lines
$lines = Get-Content $logFile -ErrorAction SilentlyContinue
if ($lines.Count -gt 500) {
    $lines | Select-Object -Last 500 | Set-Content $logFile -Encoding utf8
}
