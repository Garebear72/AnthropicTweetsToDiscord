# Registers a Windows scheduled task that runs the watcher every 10 minutes
# while you're logged in. Re-run to update; remove with:
#   Unregister-ScheduledTask -TaskName "Claude Reset Watch" -Confirm:$false

$ErrorActionPreference = "Stop"
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "reset_watcher.py"
$pyw    = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command python.exe).Source }

$action  = New-ScheduledTaskAction -Execute $pyw -Argument "`"$script`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName "Claude Reset Watch" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Forwards Claude usage-reset announcements to Discord" -Force | Out-Null

Write-Host "Scheduled task 'Claude Reset Watch' installed (every 10 minutes)."
