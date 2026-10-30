<#
    Practice Assistant - installer (Windows 10 / 11, x64)

    Run by double-clicking "Install.cmd" in the USB folder. Installs for the
    current user only: no administrator rights and no system-wide changes.

    The target PC has internet access for the OpenRouter/OpenAI API, so Python
    and the packages are downloaded when they are missing. Nothing is copied
    from a GitHub repository: all application files come from this folder.

    Re-running this script upgrades an existing installation in place.
#>

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'PracticeAssistant'),
    [switch]$NoShortcut,
    [switch]$SkipKeyPrompt,
    [switch]$Quiet,
    [string]$RepoUrl = '',
    [switch]$NoAutoUpdate
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Script:Root = Split-Path -Parent $PSScriptRoot
$Script:AppSource = Join-Path $Script:Root 'app'
$Script:Venv = Join-Path $InstallDir '.venv'
$Script:PythonExe = Join-Path $Script:Venv 'Scripts\python.exe'
$Script:LogPath = Join-Path $env:TEMP 'practice-assistant-install.log'
$Script:MinPython = [version]'3.10'

function Write-Step([string]$Message) {
    if (-not $Quiet) { Write-Host "==> $Message" -ForegroundColor Cyan }
}

function Write-Ok([string]$Message) {
    if (-not $Quiet) { Write-Host "    $Message" -ForegroundColor Green }
}

function Write-Note([string]$Message) {
    if (-not $Quiet) { Write-Host "    $Message" -ForegroundColor DarkGray }
}

function Fail([string]$Message, [string]$Hint) {
    Write-Host ''
    Write-Host 'Installation stopped.' -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    if ($Hint) { Write-Host ''; Write-Host $Hint -ForegroundColor Yellow }
    Write-Host ''
    Write-Host "Details were written to: $Script:LogPath" -ForegroundColor DarkGray
    exit 1
}

function Invoke-Logged([string]$File, [string[]]$Arguments) {
    $output = & $File @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($output) { $output | Out-File -FilePath $Script:LogPath -Append -Encoding utf8 }
    if ($code -ne 0) {
        throw ("Command failed ({0}): {1} {2}`n{3}" -f $code, $File, ($Arguments -join ' '), ($output -join "`n"))
    }
    return $output
}

try {
    Set-Content -Path $Script:LogPath -Value "Practice Assistant install log" -Encoding utf8

    # ---- 1. Preflight (nothing is changed before this passes) --------------
    Write-Step 'Checking that this PC can run the assistant'

    if (-not [Environment]::Is64BitOperatingSystem) {
        Fail 'This installer needs 64-bit Windows.' 'Use a 64-bit Windows 10 or 11 PC.'
    }
    $os = [Environment]::OSVersion.Version
    if ($os.Major -lt 10) {
        Fail "Windows $os detected. Windows 10 or 11 is required." 'Update Windows and try again.'
    }
    Write-Ok "Windows $os, 64-bit"

    if (-not (Test-Path (Join-Path $Script:AppSource 'practice_assistant'))) {
        Fail "The application files are missing from the USB folder: $Script:AppSource" `
            'Copy the whole PracticeAssistant folder to the PC (not only Install.cmd) and run it again.'
    }
    Write-Ok 'Application files found'

    $drive = (Get-Item $InstallDir -ErrorAction SilentlyContinue).PSDrive
    $free = if ($drive) { $drive.Free } else { (Get-PSDrive -Name ($env:LOCALAPPDATA.Substring(0, 1))).Free }
    if ($free -lt 700MB) {
        Fail ('At least 700 MB of free space is needed; {0:N0} MB are available.' -f ($free / 1MB)) `
            'Free some space on this drive and try again.'
    }
    Write-Ok 'Enough free disk space'

    # Unblock files copied from a USB stick (Mark-of-the-Web), otherwise
    # PowerShell scripts and the launcher may be refused.
    Get-ChildItem -Path $Script:Root -Recurse -File -ErrorAction SilentlyContinue |
        Unblock-File -ErrorAction SilentlyContinue
    Write-Ok 'Removed the USB download marker from the copied files'

    # ---- 2. Find or install Python ---------------------------------------
    Write-Step 'Looking for Python 3.10 or newer'

    $systemPython = $null
    $candidates = @()
    $launcher = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.13', '-3.12', '-3.11', '-3.10', '-3')) {
            try {
                $path = & py $version -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $path) { $candidates += $path.Trim() }
            } catch { }
        }
    }
    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
    foreach ($candidate in $candidates) {
        try {
            $raw = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $raw) {
                $version = [version]$raw.Trim()
                if ($version -ge $Script:MinPython) {
                    $systemPython = $candidate
                    break
                }
            }
        } catch { }
    }

    if ($systemPython) {
        Write-Ok "Using Python $($version) at $systemPython"
    } else {
        Write-Note 'No suitable Python was found; downloading the official installer.'
        if ($Quiet) {
            Fail 'Python 3.10+ is required and silent download is disabled.' `
                'Run Install.cmd again without -Quiet.'
        }
        $installer = Join-Path $env:TEMP 'practice-assistant-python.exe'
        $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
        Write-Note "Downloading $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        } catch {
            Fail 'Python could not be downloaded. Check the internet connection.' `
                "Install Python 3.10 or newer from python.org and run Install.cmd again.`n$($_.Exception.Message)"
        }
        Write-Note 'Installing Python for the current user (this takes a minute).'
        & $installer /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 `
            Include_test=0 Include_doc=0 Include_tcltk=1 | Out-Null
        Start-Sleep -Seconds 3
        foreach ($candidate in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'))) {
            if (Test-Path $candidate) { $systemPython = $candidate; break }
        }
        if (-not $systemPython) {
            Fail 'Python installation did not complete.' `
                'Install Python 3.10+ from python.org manually, then run Install.cmd again.'
        }
        Write-Ok "Installed Python at $systemPython"
    }

    # ---- 3. Virtual environment ------------------------------------------
    Write-Step "Preparing the private environment in $InstallDir"

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if (Test-Path $Script:PythonExe) {
        Write-Note 'An installation already exists; it will be updated.'
    } else {
        Invoke-Logged $systemPython @('-m', 'venv', $Script:Venv) | Out-Null
        if (-not (Test-Path $Script:PythonExe)) {
            Fail 'The private environment could not be created.' `
                'Check that the folder is writable and try again.'
        }
    }
    Write-Ok 'Private environment ready'

    # ---- 4. Packages ------------------------------------------------------
    Write-Step 'Installing the required packages (needs internet)'

    $requirements = Join-Path $Script:AppSource 'requirements.txt'
    Invoke-Logged $Script:PythonExe @(
        '-m', 'pip', 'install', '--disable-pip-version-check',
        '--upgrade', 'pip', '--quiet') | Out-Null
    try {
        Invoke-Logged $Script:PythonExe @(
            '-m', 'pip', 'install', '--disable-pip-version-check',
            '-r', $requirements, '--quiet') | Out-Null
    } catch {
        Fail 'The packages could not be installed.' `
            "Check the internet connection and try again.`n$($_.Exception.Message)"
    }
    Write-Ok 'Packages installed'

    # ---- 5. Application files --------------------------------------------
    Write-Step 'Copying the application'

    $target = Join-Path $InstallDir 'app'
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -Path (Join-Path $Script:AppSource '*') -Destination $target -Recurse -Force
    Get-ChildItem -Path $target -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
    Write-Ok "Application copied to $target"

    # ---- 6. Launchers and shortcuts --------------------------------------
    Write-Step 'Creating the start button'

    $launcherPath = Join-Path $InstallDir 'Start Practice Assistant.cmd'
    $launcherLines = @(
        '@echo off',
        'rem Starts the practice assistant with its chat window.',
        'setlocal',
        "cd /d `"$InstallDir\app`"",
        'rem Check GitHub for a newer version first. Never blocks starting.',
        'if not "%PA_NO_UPDATE%"=="1" (',
        '  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\update.ps1" -Quiet -InstallDir "%~dp0." 2>nul',
        ')',
        "if not defined OPENAI_API_KEY (",
        '  echo.',
        '  echo The API key is not set yet.',
        '  echo Run "Set API key.cmd" once, then start this again.',
        '  echo.',
        '  pause',
        '  exit /b 1',
        ')',
        "`"$Script:PythonExe`" -m practice_assistant --ui %*",
        'if errorlevel 1 pause'
    )
    Set-Content -Path $launcherPath -Value $launcherLines -Encoding ascii

    $keyPath = Join-Path $InstallDir 'Set API key.cmd'
    $keyLines = @(
        '@echo off',
        'rem Stores the API key for the current Windows user only.',
        'setlocal',
        'echo.',
        'echo Paste the API key and press Enter. It is stored for this Windows user only.',
        'set /p KEY=API key: ',
        'if "%KEY%"=="" (',
        '  echo Nothing was entered. The key was not changed.',
        '  pause',
        '  exit /b 1',
        ')',
        'setx OPENAI_API_KEY "%KEY%" >nul',
        'if errorlevel 1 (',
        '  echo The key could not be saved.',
        '  pause',
        '  exit /b 1',
        ')',
        'echo.',
        'echo The key was saved. Close this window and start the assistant.',
        'pause'
    )
    Set-Content -Path $keyPath -Value $keyLines -Encoding ascii

    $updatePath = Join-Path $InstallDir 'Update Practice Assistant.cmd'
    $updateLines = @(
        '@echo off',
        'rem Checks GitHub for a newer version. Safe to run at any time.',
        'setlocal',
        'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\update.ps1" -Force -InstallDir "%~dp0."',
        'echo.',
        'pause'
    )
    Set-Content -Path $updatePath -Value $updateLines -Encoding ascii
    Write-Ok 'Start, key and update helpers created'

    # ---- 6b. Automatic updates from GitHub -------------------------------
    Write-Step 'Setting up version updates from GitHub'

    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'install') | Out-Null
    Copy-Item (Join-Path $Script:Root 'install\update.ps1') `
        -Destination (Join-Path $InstallDir 'install\update.ps1') -Force `
        -ErrorAction SilentlyContinue
    if (-not (Test-Path (Join-Path $InstallDir 'install\update.ps1'))) {
        Write-Note 'The update script could not be copied; automatic updates stay off.'
    }

    if ([string]::IsNullOrWhiteSpace($RepoUrl) -and -not $SkipKeyPrompt -and -not $Quiet) {
        Write-Host ''
        Write-Host 'Optional: automatic updates from GitHub.' -ForegroundColor Cyan
        Write-Host 'If the PC may read the repository, the assistant can check for a'
        Write-Host 'newer version each time it starts. Press Enter to skip.'
        $answer = Read-Host 'GitHub repository URL (or Enter to skip)'
        if (-not [string]::IsNullOrWhiteSpace($answer)) { $RepoUrl = $answer.Trim() }
    }

    $autoUpdate = if ($NoAutoUpdate) { '0' } else { '1' }
    $configLines = @(
        '# Practice Assistant update settings. Delete this file to disable updates.',
        "RepoUrl=$RepoUrl",
        "AutoUpdate=$autoUpdate"
    )
    Set-Content -Path (Join-Path $InstallDir 'update.ini') -Value $configLines -Encoding utf8

    if ([string]::IsNullOrWhiteSpace($RepoUrl)) {
        Write-Note 'No repository configured; the assistant will not contact GitHub.'
    } elseif (-not (Get-Command 'git' -ErrorAction SilentlyContinue)) {
        Write-Note 'Git is not installed; updates will be skipped until it is.'
        Write-Note 'Install Git from git-scm.com and run "Update Practice Assistant.cmd".'
        if (-not $Quiet) {
            $want = Read-Host 'Install Git now? (y/N)'
            if ($want -match '^(y|yes)$') {
                $winget = Get-Command 'winget' -ErrorAction SilentlyContinue
                if ($winget) {
                    & $winget.Source install --id Git.Git -e --source winget `
                        --accept-package-agreements --accept-source-agreements 2>&1 |
                        Select-Object -Last 3 | ForEach-Object { Write-Note $_ }
                } else {
                    Write-Note 'winget is not available on this PC; install Git manually.'
                }
            }
        }
    } else {
        Write-Ok 'Git found; version checks are enabled'

        # One optional sign-in, so later startup checks need no prompt. The
        # token is handed to Git Credential Manager, which stores it encrypted
        # in Windows; this installer never writes it anywhere.
        $mayNeedAuth = -not $Quiet -and $env:GIT_TERMINAL_PROMPT -ne '0'
        if ($mayNeedAuth -and -not $env:PA_GITHUB_TOKEN) {
            Write-Host ''
            Write-Host 'If the repository is private, it needs a read-only token.' -ForegroundColor Cyan
            Write-Host 'Create a fine-grained token with only "Contents: read" access'
            Write-Host 'for this repository. It is stored by Windows, not by this script.'
            $wants = Read-Host 'Sign in to GitHub now? (y/N)'
            if ($wants -match '^(y|yes)$') {
                $secure = Read-Host 'Paste the read-only token (or Enter to skip)' -AsSecureString
                $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
                if ([string]::IsNullOrWhiteSpace($plain)) {
                    Write-Note 'Skipped. The update check will stay quiet until you sign in.'
                } else {
                    # Use the token once to let Git Credential Manager store it.
                    $env:PA_GITHUB_TOKEN = $plain
                    try {
                        & (Join-Path $InstallDir 'install\update.ps1') -Force -InstallDir $InstallDir |
                            ForEach-Object { Write-Note $_ }
                        Write-Ok 'Repository reachable; the token was stored by Windows'
                    } catch {
                        Write-Note "The repository could not be reached: $($_.Exception.Message)"
                    } finally {
                        Remove-Item Env:\PA_GITHUB_TOKEN -ErrorAction SilentlyContinue
                        $plain = $null
                    }
                }
            }
        } elseif (-not $Quiet) {
            try {
                & (Join-Path $InstallDir 'install\update.ps1') -Force -InstallDir $InstallDir
            } catch {
                Write-Note "The first update check failed: $($_.Exception.Message)"
            }
        }
    }

    if (-not $NoShortcut) {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath('Desktop')
        foreach ($location in @($desktop, (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {
            try {
                $shortcut = $shell.CreateShortcut((Join-Path $location 'Practice Assistant.lnk'))
                $shortcut.TargetPath = $launcherPath
                $shortcut.WorkingDirectory = $InstallDir
                $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
                $shortcut.Description = 'Speak-based language practice'
                $shortcut.Save()
            } catch {
                Write-Note "Could not create a shortcut in ${location}: $($_.Exception.Message)"
            }
        }
        Write-Ok 'Desktop and Start Menu shortcuts created'
    }

    # ---- 7. Verify --------------------------------------------------------
    Write-Step 'Checking the installation'

    Push-Location $target
    try {
        $check = & $Script:PythonExe -m practice_assistant --help 2>&1
        $checkCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($checkCode -ne 0) {
        Fail 'The assistant did not start correctly.' `
            "Send this log for help: $Script:LogPath`n$($check -join "`n")"
    }
    Write-Ok 'The assistant responds correctly'

    # ---- 8. API key (optional here, can also be done later) --------------
    $existing = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
    if (-not $SkipKeyPrompt -and [string]::IsNullOrWhiteSpace($existing) -and -not $Quiet) {
        Write-Host ''
        Write-Host 'One more step: the API key.' -ForegroundColor Cyan
        Write-Host 'The assistant needs an API key to talk to the AI service.'
        Write-Host 'You can also do this later with "Set API key.cmd".'
        $secure = Read-Host 'Paste the API key (or press Enter to skip)' -AsSecureString
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        if (-not [string]::IsNullOrWhiteSpace($plain)) {
            [Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $plain, 'User')
            $env:OPENAI_API_KEY = $plain
            Write-Ok 'Key saved for this Windows user'
        } else {
            Write-Note 'Skipped. Run "Set API key.cmd" before starting the assistant.'
        }
    }

    Write-Host ''
    Write-Host 'Done. The Practice Assistant is installed.' -ForegroundColor Green
    Write-Host ''
    Write-Host 'To use it:'
    Write-Host '  1. Open "Practice Assistant" from the Desktop or Start Menu.'
    Write-Host '  2. A browser chat window opens. Allow microphone access if asked.'
    Write-Host '  3. Wear headphones, or start with --echo-guard half-duplex.'
    Write-Host ''
    Write-Host "Installed in: $InstallDir"
    Write-Host "Log file:     $Script:LogPath"
    Write-Host ''
    exit 0
}
catch {
    Fail $_.Exception.Message 'Read the log file for details, then try again.'
}
