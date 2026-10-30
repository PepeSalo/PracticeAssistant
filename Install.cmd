@echo off
rem Practice Assistant installer - double-click this file.
rem Runs install\install.ps1 for the current user; no administrator rights needed.

setlocal
title Practice Assistant Setup

set "HERE=%~dp0"
set "SCRIPT=%HERE%install\install.ps1"

if not exist "%SCRIPT%" (
  echo.
  echo The installer file was not found:
  echo   %SCRIPT%
  echo.
  echo Copy the whole PracticeAssistant folder to this PC, not only this file.
  echo.
  pause
  exit /b 1
)

where powershell >nul 2>nul
if errorlevel 1 (
  echo.
  echo Windows PowerShell was not found on this PC.
  echo Windows 10 and 11 include it by default; repair Windows and try again.
  echo.
  pause
  exit /b 1
)

echo.
echo Starting the Practice Assistant setup...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
  echo Setup finished with a problem ^(code %CODE%^).
  echo The message above explains what to do.
) else (
  echo Setup finished successfully.
)
echo.
pause
exit /b %CODE%
