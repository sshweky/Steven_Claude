$claude = "$env:USERPROFILE\.claude"
Set-Location $claude

$status = git -C $claude status --porcelain 2>&1
if ($status) {
    git -C $claude add -A
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    git -C $claude commit -m "Auto-sync $timestamp"
    git -C $claude push origin master 2>&1
}
