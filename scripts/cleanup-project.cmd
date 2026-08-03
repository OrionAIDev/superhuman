@echo off
REM Windows shim — runs the POSIX cleanup script via Git Bash if available.
set "SCRIPT_DIR=%~dp0"

if exist "C:\Program Files\Git\bin\bash.exe" (
  "C:\Program Files\Git\bin\bash.exe" "%SCRIPT_DIR%cleanup-project.sh" %*
  exit /b %ERRORLEVEL%
)

where bash >nul 2>&1
if %ERRORLEVEL%==0 (
  bash "%SCRIPT_DIR%cleanup-project.sh" %*
  exit /b %ERRORLEVEL%
)

echo Error: Git Bash not found. Install Git for Windows or run cleanup-project.sh from WSL. >&2
exit /b 1
