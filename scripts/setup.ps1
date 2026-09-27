<#
.SYNOPSIS
    One-command Heliograph bootstrap for Windows.

.DESCRIPTION
    1. Makes sure uv (https://docs.astral.sh/uv/) is installed - installs it with the
       official installer, pinned to uv 0.12.1, after telling you and asking first.
    2. Runs `uv sync` to create .venv with every dependency.
    3. Runs `heliograph setup` (checks Edge/Chrome, ffmpeg, the Instagram Store app...).

    Safe to run again at any time. No secrets are read, written or sent.

.PARAMETER Yes
    Do not prompt; install uv if missing and answer yes in `heliograph setup`.

.PARAMETER NoInput
    Never prompt and answer no (for scripts/CI): uv is not installed if missing, and
    `heliograph setup --no-input` offers nothing.

.PARAMETER WithWhisper
    Also pre-download the Whisper speech model (~500 MB) during setup.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

    Stock Windows blocks unsigned scripts (execution policy "Restricted"); -ExecutionPolicy
    Bypass applies to this one run only and changes no settings.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Yes -WithWhisper
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$WithWhisper,
    [switch]$NoInput
)

$ErrorActionPreference = 'Stop'

# The uv installer is pinned to a known release (not "latest") so the script that runs is
# the one this repository was tested with. Bump both together.
$UvVersion = '0.12.1'
$UvInstaller = "https://astral.sh/uv/$UvVersion/install.ps1"

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  ok  $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  !!  $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "  xx  $Text" -ForegroundColor Red }

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Write-Host "Heliograph setup in $RepoRoot" -ForegroundColor White

# --- 1. uv -------------------------------------------------------------------------------
Write-Step 'Checking for uv'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Warn 'uv is not installed.'
    Write-Host "      It will be installed with the official Astral installer (uv $UvVersion):"
    Write-Host "        irm $UvInstaller | iex" -ForegroundColor Gray
    Write-Host '      (installs to %USERPROFILE%\.local\bin, no admin rights needed)'
    if ($NoInput -and -not $Yes) {
        Write-Fail 'uv is required and -NoInput was given. Install it (https://docs.astral.sh/uv/) and re-run.'
        exit 1
    }
    if (-not $Yes) {
        $answer = Read-Host '      Install uv now? [Y/n]'
        if ($answer -and $answer -notmatch '^(y|yes)$') {
            Write-Fail 'uv is required. Install it yourself (https://docs.astral.sh/uv/) and re-run.'
            exit 1
        }
    }
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm $UvInstaller | iex"
    foreach ($dir in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
        if ((Test-Path $dir) -and ($env:Path -notlike "*$dir*")) { $env:Path = "$dir;$env:Path" }
    }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Fail 'uv was installed but is not on PATH yet. Open a new terminal and re-run.'
        exit 1
    }
}
Write-Ok "uv $((& uv --version) -replace '^uv ', '')"

# --- 2. dependencies -----------------------------------------------------------------------
Write-Step 'Installing Python dependencies (uv sync, with on-screen OCR extras)'
& uv sync --locked --extra ocr --extra ocr-windows  # --locked: exactly what uv.lock pins
if ($LASTEXITCODE -ne 0) { Write-Fail 'uv sync failed (see above).'; exit $LASTEXITCODE }
Write-Ok 'dependencies installed in .venv'

# --- 3. heliograph setup ---------------------------------------------------------------------
Write-Step 'Checking this machine (heliograph setup)'
$setupArgs = @('run', '--quiet', 'heliograph', 'setup')
if ($Yes) { $setupArgs += '--yes' }
if ($WithWhisper) { $setupArgs += '--with-whisper' }
if ($NoInput) { $setupArgs += '--no-input' }
& uv @setupArgs
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host ''
    Write-Ok 'Setup finished.'
    # Only suggest `login` when the dedicated profile has never been signed in (or was last
    # seen logged out); doctor --json reports that without opening any browser.
    $needsLogin = $true
    try {
        $doctor = (& uv run --quiet heliograph doctor --json 2>$null | Out-String) | ConvertFrom-Json
        if ($null -ne $doctor.needs_login) { $needsLogin = [bool]$doctor.needs_login }
    } catch { $needsLogin = $true }
    if ($needsLogin) {
        Write-Host '      Next: uv run heliograph login   then run  claude  in this folder.' -ForegroundColor White
    } else {
        Write-Host '      Browser profile already signed in. Next: run  claude  in this folder.' -ForegroundColor White
    }
} else {
    Write-Fail 'Setup found a critical problem (see above). Fix it and re-run this script.'
}
exit $code
