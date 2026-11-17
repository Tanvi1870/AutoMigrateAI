#Requires -Version 5.1
<#
.SYNOPSIS
  Sets up the AutoMigrate AI backend: virtual environment, dependencies and .env.
.PARAMETER InstallBrowsers
  Also download Chromium for Playwright (needs internet / proxy access).
#>
param([switch]$InstallBrowsers)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-Checked([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $exe $arguments" }
}

Push-Location $root
try {
    if (-not (Test-Path $python)) { Invoke-Checked 'python' @('-m', 'venv', '.venv') }
    Invoke-Checked $python @('-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Checked $python @('-m', 'pip', 'install', '--require-hashes', '-r', 'backend\requirements-dev.lock')
    if ($InstallBrowsers) { Invoke-Checked $python @('-m', 'playwright', 'install', 'chromium') }
    if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
    Write-Host 'Setup complete. Run: .\.venv\Scripts\Activate.ps1' -ForegroundColor Green
}
finally {
    Pop-Location
}
