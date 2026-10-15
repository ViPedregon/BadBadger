param([switch]$Clean)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$environment = Join-Path $projectRoot ".build-venv"
$python = Join-Path $environment "Scripts\python.exe"
$temporary = Join-Path $projectRoot ".build-tmp"
New-Item -ItemType Directory -Force $temporary | Out-Null
$env:TEMP = $temporary
$env:TMP = $temporary

if ($Clean) {
    foreach ($name in @("build", "dist", ".build-venv", ".build-tmp", "BadBadger.spec")) {
        $target = Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Force $temporary | Out-Null
}

if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv $environment
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $python -m pip install --disable-pip-version-check --no-build-isolation `
    -e "$projectRoot[openai]" pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --onefile --name BadBadger `
        --collect-data badbadger src\badbadger\cli.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "Built dist\BadBadger.exe"
