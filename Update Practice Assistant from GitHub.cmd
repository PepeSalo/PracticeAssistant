@echo off
rem ===================================================================
rem  Practice Assistant - get the newest version from GitHub
rem
rem  Copy this one file to the Desktop and double-click it.
rem  It updates the copy already installed on this PC.
rem  Nothing is installed again and your API key is not touched.
rem ===================================================================

setlocal
title Update Practice Assistant

echo.
echo  ==================================================
echo   Practice Assistant - get the newest version
echo  ==================================================
echo.

set "INSTALL=%LOCALAPPDATA%\PracticeAssistant"
set "REPO=https://github.com/PepeSalo/PracticeAssistant"

rem Optional override, used for testing a copy in another folder.
if defined PA_INSTALL set "INSTALL=%PA_INSTALL%"

rem ---- 1. Is the assistant installed? --------------------------------
if not exist "%INSTALL%\app\practice_assistant" (
    echo  Practice Assistant is not installed on this PC yet.
    echo.
    echo  To install it:
    echo    1. Plug in the USB stick.
    echo    2. Open the PracticeAssistant folder on the stick.
    echo    3. Double-click Install.cmd
    echo.
    echo  Then run this file again.
    echo.
    pause
    exit /b 1
)

rem ---- 2. Is the updater present? ------------------------------------
if not exist "%INSTALL%\install\update.ps1" (
    echo  The updater is missing from the installation.
    echo.
    echo  Run Install.cmd from the USB stick once to repair it,
    echo  then run this file again.
    echo.
    pause
    exit /b 1
)

rem ---- 3. PowerShell available? --------------------------------------
where powershell >nul 2>nul
if errorlevel 1 (
    echo  Windows PowerShell was not found on this PC.
    echo  Windows 10 and 11 include it by default.
    echo  Ask someone to repair Windows, then try again.
    echo.
    pause
    exit /b 1
)

rem ---- 4. Make sure the updater knows where to look ----------------
rem  The GitHub address may have been skipped during installation.
findstr /i /c:"RepoUrl=https" "%INSTALL%\update.ini" >nul 2>nul
if errorlevel 1 (
    >"%INSTALL%\update.ini" (
        echo # Practice Assistant update settings.
        echo RepoUrl=%REPO%
        echo AutoUpdate=1
    )
    echo  Saved the GitHub address for future updates.
    echo.
)

rem ---- 5. Update ------------------------------------------------------
echo  Looking for a newer version on GitHub...
echo  (This usually takes only a few seconds.)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALL%\install\update.ps1" -Force -InstallDir "%INSTALL%"
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
    echo  All done.
    echo.
    echo  Start the assistant from the "Practice Assistant"
    echo  icon on the Desktop.
) else (
    echo  The update did not finish.
    echo  Your installed version still works normally.
    echo.
    echo  If this repeats, check the internet connection,
    echo  then try again.
)

echo.
pause
exit /b 0
