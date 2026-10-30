<#
    Practice Assistant - update from a GitHub repository.

    Called by "Start Practice Assistant.cmd" before the program starts, and by
    "Update Practice Assistant.cmd" on demand.

    Safety rules:
      - it never blocks starting the program: every problem is a warning and
        the installed copy keeps working;
      - it only fast-forwards (git pull --ff-only), so local edits are never
        destroyed and diverged branches are reported instead of merged;
      - uncommitted local changes are left alone;
      - credentials are never written by this script and never stored in
        update.ini or .git/config. A private repository is authenticated by
        Git Credential Manager (encrypted by Windows), or by the optional
        PA_GITHUB_TOKEN environment variable, which is used in memory only.
        Use a read-only token scoped to this one repository.

    Set PA_NO_UPDATE=1 (or pass -NoUpdate to the launcher path) to skip it.
#>

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'PracticeAssistant'),
    [switch]$Quiet,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$configPath = Join-Path $InstallDir 'update.ini'
$repoDir = Join-Path $InstallDir 'repo'
$appDir = Join-Path $InstallDir 'app'
$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
$logPath = Join-Path $InstallDir 'update.log'

function Note([string]$Message) {
    if (-not $Quiet) { Write-Host "    $Message" -ForegroundColor DarkGray }
    Add-Content -Path $logPath -Value "$(Get-Date -Format s)  $Message" -Encoding utf8 -ErrorAction SilentlyContinue
}

function Warn([string]$Message) {
    Write-Host "  Update skipped: $Message" -ForegroundColor Yellow
    Note "WARNING $Message"
}

function Read-Config {
    $result = @{ RepoUrl = ''; AutoUpdate = '1' }
    if (-not (Test-Path $configPath)) { return $result }
    foreach ($line in Get-Content $configPath -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*([A-Za-z]+)\s*=\s*(.*)$') {
            $result[$Matches[1]] = $Matches[2].Trim()
        }
    }
    return $result
}


function Get-GitAuthArguments {
    <#
        Returns git arguments that authenticate a private repository.

        Priority:
          1. PA_GITHUB_TOKEN (environment variable, memory only)
          2. Git Credential Manager (Windows-encrypted credential store)

        The token is passed as an HTTP header and never written to disk,
        never placed in the remote URL and never written to the log.
    #>
    $arguments = @()

    $manager = Get-Command 'git-credential-manager' -ErrorAction SilentlyContinue
    if ($manager) {
        # Use the encrypted Windows credential store explicitly, because a
        # portable Git may not have configured it globally.
        $arguments += @('-c', 'credential.helper=manager')
    }

    $token = $env:PA_GITHUB_TOKEN
    if ([string]::IsNullOrWhiteSpace($token)) { return $arguments }

    $pair = "x-access-token:$token"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
    $arguments += @('-c', "http.extraheader=Authorization: Basic $encoded")
    return $arguments
}

try {
    Add-Content -Path $logPath -Value "$(Get-Date -Format s)  update check started" -Encoding utf8 -ErrorAction SilentlyContinue

    if ($env:PA_NO_UPDATE -eq '1') {
        Note 'Update check disabled by PA_NO_UPDATE.'
        exit 0
    }
    if (-not (Test-Path $appDir)) {
        Note 'The application is not installed yet; there is nothing to update.'
        exit 0
    }

    $config = Read-Config
    if ([string]::IsNullOrWhiteSpace($config.RepoUrl)) {
        Note 'No repository is configured; skipping the update check.'
        exit 0
    }
    if ($config.AutoUpdate -ne '1' -and -not $Force) {
        Note 'Automatic updates are turned off; use "Update Practice Assistant.cmd".'
        exit 0
    }

    $git = Get-Command 'git' -ErrorAction SilentlyContinue
    if (-not $git) {
        Warn 'Git is not installed on this PC, so the latest version cannot be checked.'
        exit 0
    }
    Note "Using $($git.Source)"

    $url = $config.RepoUrl
    # A startup check must never block waiting for a password prompt; the
    # on-demand updater may ask once, and Git stores that credential itself.
    $env:GIT_TERMINAL_PROMPT = if ($Quiet) { '0' } else { '1' }
    $auth = Get-GitAuthArguments
    if ($env:PA_GITHUB_TOKEN) {
        Note 'Using PA_GITHUB_TOKEN from the environment (read-only token).'
    }

    # ---- first clone ------------------------------------------------------
    if (-not (Test-Path (Join-Path $repoDir '.git'))) {
        Note 'Downloading the application for the first time.'
        if (Test-Path $repoDir) { Remove-Item -Recurse -Force $repoDir }
        $clone = & $git.Source @auth clone --quiet $url $repoDir 2>&1
        if ($LASTEXITCODE -ne 0) {
            Warn "the repository could not be reached. $($clone -join ' ')"
            exit 0
        }
    } else {
        Note 'Checking GitHub for a newer version.'
        $status = & $git.Source @auth -C $repoDir status --porcelain 2>&1
        if ($LASTEXITCODE -ne 0) {
            Warn "the local copy is not a usable repository. $($status -join ' ')"
            exit 0
        }
        if ($status) {
            Warn 'the local copy contains manual changes; it was left untouched.'
            exit 0
        }
        $pull = & $git.Source @auth -C $repoDir pull --ff-only --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            Warn "the update could not be applied (offline or changed upstream). $($pull -join ' ')"
            exit 0
        }
    }

    $repoApp = Join-Path $repoDir 'practice_assistant'
    if (-not (Test-Path $repoApp)) {
        Warn 'the repository does not contain the application; nothing was changed.'
        exit 0
    }

    # ---- version comparison ----------------------------------------------
    $installedVersion = ''
    $installedPyproject = Join-Path $appDir 'pyproject.toml'
    if (Test-Path $installedPyproject) {
        $installedVersion = (Select-String -Path $installedPyproject -Pattern '^version\s*=\s*"([^"]+)"').Matches.Groups[1].Value
    }
    $availableVersion = ''
    $repoPyproject = Join-Path $repoDir 'pyproject.toml'
    if (Test-Path $repoPyproject) {
        $availableVersion = (Select-String -Path $repoPyproject -Pattern '^version\s*=\s*"([^"]+)"').Matches.Groups[1].Value
    }

    if ($installedVersion -and $availableVersion -and ($installedVersion -eq $availableVersion)) {
        Note "Already up to date (version $installedVersion)."
        exit 0
    }

    # ---- copy the new files ---------------------------------------------
    Note "Updating from version '$installedVersion' to '$availableVersion'."
    New-Item -ItemType Directory -Force -Path $appDir | Out-Null
    foreach ($item in @('practice_assistant', 'requirements.txt', 'pyproject.toml', 'readme.md')) {
        $source = Join-Path $repoDir $item
        $destination = Join-Path $appDir $item
        if (-not (Test-Path $source)) { continue }
        if (Test-Path $destination) { Remove-Item -Recurse -Force $destination }
        Copy-Item $source -Destination $destination -Recurse -Force
    }
    Get-ChildItem -Path $appDir -Recurse -File -ErrorAction SilentlyContinue |
        Unblock-File -ErrorAction SilentlyContinue

    # ---- dependencies may have changed ----------------------------------
    if ((Test-Path $python) -and (Test-Path (Join-Path $appDir 'requirements.txt'))) {
        Note 'Checking that the required packages are still complete.'
        $pip = & $python -m pip install --disable-pip-version-check --quiet `
            -r (Join-Path $appDir 'requirements.txt') 2>&1
        if ($LASTEXITCODE -ne 0) {
            Warn "the packages could not be refreshed. $($pip -join ' ')"
        }
    }

    Write-Host "  Updated to version $availableVersion." -ForegroundColor Green
    Note "updated to $availableVersion"
    exit 0
}
catch {
    Warn $_.Exception.Message
    exit 0
}
