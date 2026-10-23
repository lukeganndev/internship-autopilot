<#
Registers Internship Autopilot's Windows scheduled tasks for the current user (no admin needed).

  "Internship Autopilot - Source"  every 2 hours while logged on, 2 minutes after logon,
                                   and a catch-up run if a scheduled time was missed.
  "Internship Autopilot - Tailor"  9:00 AM and 6:00 PM daily (catch-up if the PC was off):
                                   tailors resumes for approved jobs with `claude -p`.

Runs .venv\Scripts\pythonw.exe (no console window). Logs: ~\.internship-autopilot\data\logs\
Re-run any time to update. Remove both with:  .\scripts\install_tasks.ps1 -Uninstall
#>
param([switch]$Uninstall)

$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$source = "Internship Autopilot - Source"
$tailor = "Internship Autopilot - Tailor"

if ($Uninstall) {
    foreach ($n in $source, $tailor) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue; "Removed '$n'" }
    return
}
if (-not (Test-Path $pythonw)) { throw "Run 'uv sync' first ($pythonw not found)." }

function New-Settings($limitMinutes) {
    New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $limitMinutes) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m iap.task source" -WorkingDirectory $repo
$every2h = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours 2)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = "PT2M"  # give Wi-Fi a moment after login
Register-ScheduledTask -TaskName $source -Action $action -Trigger $every2h, $logon -Settings (New-Settings 30) `
    -Description "Fetch new internship postings (github.com/lukeganndev/internship-autopilot)" -Force | Out-Null
"Registered '$source'. Next run: $((Get-ScheduledTaskInfo -TaskName $source).NextRunTime)"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m iap.task tailor" -WorkingDirectory $repo
$morning = New-ScheduledTaskTrigger -Daily -At "9:00AM"
$evening = New-ScheduledTaskTrigger -Daily -At "6:00PM"
Register-ScheduledTask -TaskName $tailor -Action $action -Trigger $morning, $evening -Settings (New-Settings 60) `
    -Description "Tailor resumes for approved internships with Claude Code (github.com/lukeganndev/internship-autopilot)" -Force | Out-Null
"Registered '$tailor'. Next run: $((Get-ScheduledTaskInfo -TaskName $tailor).NextRunTime)"
