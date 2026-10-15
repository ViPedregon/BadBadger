param(
    [switch]$NewGame,
    [switch]$Offline,
    [switch]$Developer
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$environment = Join-Path $projectRoot ".venv"
$python = Join-Path $environment "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -eq $launcher) {
        Write-Host "BadBadger needs Python 3.11 or newer for this source checkout."
        Write-Host "Install it from https://www.python.org/downloads/windows/ and run play.cmd again."
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Preparing BadBadger for first play..."
    & py -3 -m venv $environment
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m pip install --disable-pip-version-check -e "$projectRoot[openai]"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$saveDirectory = Join-Path $projectRoot "saves"
New-Item -ItemType Directory -Force $saveDirectory | Out-Null
$arguments = @("-m", "badbadger.cli", "--save", (Join-Path $saveDirectory "relay-crisis.db"))
if ($NewGame) { $arguments += "--new-game" }
if ($Offline) { $arguments += "--offline" }
if ($Developer) { $arguments += "--developer" }

& $python @arguments
Read-Host "Press Enter to close"
exit $LASTEXITCODE
