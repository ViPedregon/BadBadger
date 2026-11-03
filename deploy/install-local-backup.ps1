param(
    [string]$Origin = 'https://badbadger.onrender.com',
    [string]$Destination = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'BadBadgerBackups'),
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [string]$TaskName = 'BadBadger shared save backup'
)
$ErrorActionPreference = 'Stop'
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$runner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'download-backup.ps1')).Path
$Destination = [IO.Path]::GetFullPath($Destination)
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'A task with that name exists. Review or remove it before installing another.'
}
$privateDirectory = Join-Path $env:LOCALAPPDATA 'BadBadgerBackup'
New-Item -ItemType Directory -Path $privateDirectory -Force | Out-Null
$credentialFile = Join-Path $privateDirectory ('token-' + [guid]::NewGuid().ToString('N') + '.xml')
$token = Read-Host 'Enter the separate Render BADBADGER_BACKUP_TOKEN' -AsSecureString
$credential = New-Object System.Management.Automation.PSCredential('backup', $token)
$credential | Export-Clixml -LiteralPath $credentialFile
# Test the real download before enabling unattended execution.
$powershell = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
if ([IO.Path]::GetFileName($powershell) -notin @('powershell.exe', 'pwsh.exe')) {
    throw 'Run the installer from a Windows PowerShell or PowerShell 7 terminal.'
}
& $powershell -NoProfile -NonInteractive -File $runner -Origin $Origin -Destination $Destination -CredentialFile $credentialFile -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { throw 'Initial backup failed. No scheduled task was installed.' }
foreach ($argument in @($runner, $Origin, $Destination, $credentialFile, $PythonPath)) {
    if ($argument.Contains('"')) { throw 'Double quotes are not supported in task argument paths.' }
}
$arguments = '-NoProfile -NonInteractive -File "{0}" -Origin "{1}" -Destination "{2}" -CredentialFile "{3}" -PythonPath "{4}"' -f $runner, $Origin, $Destination, $credentialFile, $PythonPath
$action = New-ScheduledTaskAction -Execute $powershell -Argument ($arguments + ' -NotifyOnFailure')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Write-Output "Hourly task installed. Backups: $Destination. Runs while you are signed in; missed runs start when available."
