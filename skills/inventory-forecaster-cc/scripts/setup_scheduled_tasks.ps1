# setup_scheduled_tasks.ps1
# Registers both scheduled tasks in Windows Task Scheduler.
# Run once as Administrator on the target machine.
#
# Tasks registered:
#   PP_Monday_CategoryProfiles   -- Monday 7:15am, build_category_profiles_from_report.py
#   PP_Daily_AlertDismiss        -- Daily   7:20am, dismiss_reviewed_alerts.py
#   PP_Daily_AITrainingReview    -- Daily   6:00am, ai_training_review.py

$Python    = "C:\Python314\python.exe"
$ScriptDir = $PSScriptRoot                       # ..\scripts\
$SkillRoot = Split-Path -Parent $ScriptDir       # skill root

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit     (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# -- Task 1: Monday category profile build (7:15am) --
$Action1  = New-ScheduledTaskAction `
    -Execute          $Python `
    -Argument         "`"$SkillRoot\build_category_profiles_from_report.py`"" `
    -WorkingDirectory $SkillRoot
$Trigger1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "7:15AM"

Register-ScheduledTask `
    -TaskName  "PP_Monday_CategoryProfiles" `
    -Action    $Action1 `
    -Trigger   $Trigger1 `
    -Settings  $Settings `
    -RunLevel  Highest `
    -Force
Write-Host "Registered: PP_Monday_CategoryProfiles  (Mon 7:15am)" -ForegroundColor Green

# -- Task 2: Daily alert dismiss (7:20am) --
$Action2  = New-ScheduledTaskAction `
    -Execute          $Python `
    -Argument         "`"$ScriptDir\dismiss_reviewed_alerts.py`"" `
    -WorkingDirectory $ScriptDir
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "7:20AM"

Register-ScheduledTask `
    -TaskName  "PP_Daily_AlertDismiss" `
    -Action    $Action2 `
    -Trigger   $Trigger2 `
    -Settings  $Settings `
    -RunLevel  Highest `
    -Force
Write-Host "Registered: PP_Daily_AlertDismiss       (Daily 7:20am)" -ForegroundColor Green

# -- Task 3: Daily AI Training Review (6:00am) --
$Action3  = New-ScheduledTaskAction `
    -Execute          $Python `
    -Argument         "`"$ScriptDir\ai_training_review.py`"" `
    -WorkingDirectory $ScriptDir
$Trigger3 = New-ScheduledTaskTrigger -Daily -At "6:00AM"

Register-ScheduledTask `
    -TaskName  "PP_Daily_AITrainingReview" `
    -Action    $Action3 `
    -Trigger   $Trigger3 `
    -Settings  $Settings `
    -RunLevel  Highest `
    -Force
Write-Host "Registered: PP_Daily_AITrainingReview    (Daily 6:00am)" -ForegroundColor Green

Write-Host "`nVerify all tasks in Task Scheduler (taskschd.msc)." -ForegroundColor Cyan
