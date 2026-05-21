$claude = "$env:USERPROFILE\.claude"
$logFile = "$claude\git-sync.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Out-File -Append -Encoding utf8 -FilePath $logFile
}

Set-Location $claude

# Stage and commit any local changes
$status = git -C $claude status --porcelain 2>&1
if ($status) {
    git -C $claude add -A 2>&1 | Out-Null
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $commitOut = git -C $claude commit -m "Auto-sync $timestamp" 2>&1
    Log "Committed: $($commitOut -join ' ')"
} else {
    Log "No local changes to commit"
}

# Stash any remaining unstaged changes (e.g. log file written during this script)
# so that pull --rebase does not get blocked
$stashOut = git -C $claude stash 2>&1
$stashed = $stashOut -notmatch "No local changes"

# Pull remote changes with rebase
$pullOut = git -C $claude pull --rebase origin master 2>&1
Log "Pull: $($pullOut -join ' | ')"

# Restore stash if we stashed anything
if ($stashed) {
    git -C $claude stash pop 2>&1 | Out-Null
}

# Push to GitHub
$pushOut = git -C $claude push origin master 2>&1
Log "Push: $($pushOut -join ' | ')"

# Trim log to last 500 lines to prevent unbounded growth
$lines = Get-Content $logFile -ErrorAction SilentlyContinue
if ($lines -and $lines.Count -gt 500) {
    $lines | Select-Object -Last 500 | Set-Content $logFile -Encoding utf8
}
