# Back up the Render shared game to this Windows PC

The game stays on Render. An hourly Windows task pulls a fresh, verified
snapshot while this PC is awake and the installing user is signed in. It uses
HTTPS with certificate validation, a separate backup-only token, and checksums.
Downloads are validated as BadBadger saves before being published locally.
Existing backups are never replaced or automatically deleted.

## Enable the server endpoint

Deploy the implementation containing `/api/backup` to the Render service.
Set `BADBADGER_BACKUP_TOKEN` securely in its environment to a newly generated
random token of at least 32 characters, distinct from the game password.
For example, generate a value privately with Python's
`secrets.token_urlsafe(48)` and enter it directly into the host's secret setting.
Do not put the token in Git, chat, URLs, task arguments or browser storage.
Restart/redeploy the service with that environment variable present.

Without this setting the download endpoint returns 404. Game Basic-auth
credentials do not grant backup access. The token cannot play the game, upload
saves or restore state. This endpoint exports only the shared game and is
disabled in tester-account mode. It allows at most six authorized downloads per
hour, plus separate failed-authentication limits.

Each request creates a temporary SQLite snapshot containing committed WAL
changes, validates it and downloads it with a SHA-256 checksum header. The
temporary snapshot is removed after transfer. Backups contain full world state,
private character information and dialogue: keep the local folder private.

## Install on Windows

Use a stable checkout and Python environment with BadBadger installed. Do not
move either after registering the task. This repository's prepared environment
is `.build-venv\Scripts\python.exe`; a separately installed `.venv` also works.

From a PowerShell session in the repository directory:

```powershell
.\deploy\install-local-backup.ps1 `
  -PythonPath .\.build-venv\Scripts\python.exe `
  -Destination "$env:USERPROFILE\Documents\BadBadgerBackups"
```

The default origin is `https://badbadger.onrender.com`. The script prompts for
the **backup** token without echoing it, stores a Windows DPAPI-encrypted
credential under `%LOCALAPPDATA%\BadBadgerBackup`, and attempts a real download.
Only after that succeeds does it install `BadBadger shared save backup` in
Windows Task Scheduler. No administrator privileges are requested. Local script
execution must be permitted by your existing PowerShell policy; the installer
does not change that policy.

The task runs hourly with limited privileges as your signed-in Windows user;
missed runs start when available. It does not wake the PC or run while signed
out. Windows protects the credential for the installing user on this machine;
copying its XML to another machine is not a credential migration strategy.
The task never receives the token as a command-line argument. The runner exposes
it only to the downloader's process environment and removes it afterward.

## Check, recover and maintain

```powershell
Get-ScheduledTaskInfo -TaskName 'BadBadger shared save backup'
Start-ScheduledTask -TaskName 'BadBadger shared save backup'
Get-ChildItem "$env:USERPROFILE\Documents\BadBadgerBackups" -Filter *.db
```

After a run finishes, `LastTaskResult` should be zero and a new timestamped
`shared-*.db` file should appear. Nonzero means inspect connectivity, the Render
deployment/token, Python installation and folder permissions. No separate email
notifications are configured. The scheduled runner attempts a Windows message
in your signed-in session when a download fails. If Windows cannot display it,
Task Scheduler still records the failure. Files are retained indefinitely;
monitor disk space and choose a retention policy after a restore drill succeeds.

Use `badbadger-save restore --source PATH-TO-LOCAL-BACKUP --destination NEW-PATH`
to prepare a recovery candidate. Run a separate local offline game against it
and confirm the transcript and next turn. Never overwrite the live Render save;
follow the maintenance/recovery process in README.md before activating a recovery
candidate on the server. Restore back to Render is a separate manual operation.

When the PC is offline, no offsite backups are made. On reconnection the task
downloads the **current** save; it cannot recreate missed hourly history. If you
need backups while the PC is unavailable, also configure server-side retention
or another always-on destination. Backups on this PC protect against a separate
Render-host loss, but are not themselves another copy of this PC's disk.

To rotate the token, update Render, unregister the old scheduled task, and rerun
the installer with the new value. Unregistering does not delete backups or the
encrypted credential file; retain or remove those deliberately. No actual token,
Render deployment, live download or scheduled task is created by checking these
scripts into the repository.
