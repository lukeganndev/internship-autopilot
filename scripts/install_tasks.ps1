<#
Registers Internship Autopilot's Windows scheduled tasks for the current user (no admin needed).

  "Internship Autopilot - Source"  every 2 hours while logged on, 2 minutes after logon,
                                   and a catch-up run if a scheduled time was missed.

Runs .venv\Scripts\pythonw.exe (no console window). Logs: ~\.internship-autopilot\data\logs\
Re-run any time to update. Remove with:  .\scripts\install_tasks.ps1 -Uninstall
#>
param([switch]$Uninstall)

$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$name = "Internship Autopilot - Source"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    "Removed '$name'"
    return
}
if (-not (Test-Path $pythonw)) { throw "Run 'uv sync' first ($pythonw not found)." }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m iap.task source" -WorkingDirectory $repo
$every2h = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Hours 2)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = "PT2M"  # give Wi-Fi a moment after login
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $name -Action $action -Trigger $every2h, $logon -Settings $settings `
    -Description "Fetch new internship postings (github.com/lukeganndev/internship-autopilot)" -Force | Out-Null
"Registered '$name'. Next run: $((Get-ScheduledTaskInfo -TaskName $name).NextRunTime)"
