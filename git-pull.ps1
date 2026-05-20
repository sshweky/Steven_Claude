$claude = "$env:USERPROFILE\.claude"
$result = git -C $claude pull origin master 2>&1
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content "$claude\git-sync.log" "$timestamp [PULL] $result"
