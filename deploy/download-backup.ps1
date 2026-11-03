param(
    [string]$Origin = 'https://badbadger.onrender.com',
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$CredentialFile,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [switch]$NotifyOnFailure
)
$ErrorActionPreference = 'Stop'
try {
    # Export-Clixml uses Windows DPAPI: only this Windows user on this PC can
    # decrypt the credential. The token is never passed on a command line.
    $credential = Import-Clixml -LiteralPath $CredentialFile
    if ($credential -isnot [System.Management.Automation.PSCredential]) { throw 'Invalid credential file' }
    $env:BADBADGER_BACKUP_TOKEN = $credential.GetNetworkCredential().Password
    & $PythonPath -m badbadger.download_backup --origin $Origin --destination $Destination
    $backupExit = $LASTEXITCODE
} catch {
    Write-Error 'Backup setup or download failed. Check the private credential file and Python installation.' -ErrorAction Continue
    $backupExit = 1
} finally {
    Remove-Item Env:BADBADGER_BACKUP_TOKEN -ErrorAction SilentlyContinue
}
if ($backupExit -ne 0 -and $NotifyOnFailure) {
    try {
        $messageTool = Join-Path $env:SystemRoot 'System32\msg.exe'
        $sessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
        & $messageTool $sessionId /time:60 'BadBadger backup failed. Check the backup task in Windows Task Scheduler.'
    } catch {
        Write-Warning 'Windows could not display the backup failure alert.'
    }
}
exit $backupExit
