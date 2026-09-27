<#
.SYNOPSIS
    One-command Heliograph bootstrap for Windows.

.DESCRIPTION
    1. Makes sure uv (https://docs.astral.sh/uv/) is installed - installs it with the
       official installer after telling you and asking first.
    2. Runs `uv sync` to create .venv with every dependency.
    3. Runs `heliograph setup` (checks Edge/Chrome, ffmpeg, the Instagram Store app...).

    Safe to run again at any time. No secrets are read, written or sent.

.PARAMETER Yes
    Do not prompt; install uv if missing and answer yes in `heliograph setup`.

.PARAMETER WithWhisper
    Also pre-download the Whisper speech model (~500 MB) during setup.

.EXAMPLE
    ./scripts/setup.ps1
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$WithWhisper
)

$ErrorActionPreference = 'Stop'

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
    Write-Host '      It will be installed with the official Astral installer:'
    Write-Host '        irm https://astral.sh/uv/install.ps1 | iex' -ForegroundColor Gray
    Write-Host '      (installs to %USERPROFILE%\.local\bin, no admin rights needed)'
    if (-not $Yes) {
        $answer = Read-Host '      Install uv now? [Y/n]'
        if ($answer -and $answer -notmatch '^(y|yes)$') {
            Write-Fail 'uv is required. Install it yourself (https://docs.astral.sh/uv/) and re-run.'
            exit 1
        }
    }
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
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
& uv sync --extra ocr --extra ocr-windows
if ($LASTEXITCODE -ne 0) { Write-Fail 'uv sync failed (see above).'; exit $LASTEXITCODE }
Write-Ok 'dependencies installed in .venv'

# --- 3. heliograph setup ---------------------------------------------------------------------
Write-Step 'Checking this machine (heliograph setup)'
$setupArgs = @('run', '--quiet', 'heliograph', 'setup')
if ($Yes) { $setupArgs += '--yes' }
if ($WithWhisper) { $setupArgs += '--with-whisper' }
& uv @setupArgs
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host ''
    Write-Ok 'Setup finished.'
    Write-Host '      Next: uv run heliograph login   then run  claude  in this folder.' -ForegroundColor White
} else {
    Write-Fail 'Setup found a critical problem (see above). Fix it and re-run this script.'
}
exit $code
