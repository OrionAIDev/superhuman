@echo off
REM Superhuman SessionStart hook (Windows shim)
REM Runs the bash version via Git Bash if available, otherwise falls back to PowerShell-based read.
REM NOTE: We check for Git Bash explicitly (not WSL bash) because WSL bash cannot
REM       access Windows paths, and its exit codes do not propagate correctly through cmd.exe.

set "SKILL_ROOT=%~dp0.."

REM Try Git Bash at common install locations
set "GIT_BASH="
if exist "C:\Program Files\Git\bin\bash.exe" set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"
if "%GIT_BASH%"=="" if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "GIT_BASH=C:\Program Files (x86)\Git\bin\bash.exe"

if not "%GIT_BASH%"=="" (
  "%GIT_BASH%" "%SKILL_ROOT%/hooks/session-start"
  exit /b %ERRORLEVEL%
)

REM Fallback: PowerShell version
powershell -NoProfile -Command ^
  "$root = '%SKILL_ROOT%';" ^
  "function ReadFile($p) { if (Test-Path $p) { Write-Output ('### ' + $p); Get-Content -Raw $p; Write-Output '' } }" ^
  "ReadFile (Join-Path $root 'adaptation\dispatch.md');" ^
  "Get-ChildItem (Join-Path $root 'conventions\*.md') | ForEach-Object { ReadFile $_.FullName };" ^
  "ReadFile (Join-Path $root 'roles\pm.md');" ^
  "Get-ChildItem (Join-Path $root 'roles\*.md') | Where-Object { $_.Name -ne 'pm.md' } | ForEach-Object { ReadFile $_.FullName };" ^
  "Get-ChildItem (Join-Path $root 'phases\*.md') | Sort-Object Name | ForEach-Object { ReadFile $_.FullName };" ^
  "ReadFile (Join-Path $root 'templates\gate-headers.md');" ^
  "ReadFile (Join-Path $root 'templates\delta-report.md.tpl');" ^
  "ReadFile (Join-Path $root 'templates\SUPERHUMAN.md.tpl');" ^
  "Get-ChildItem (Join-Path $root 'references') -Directory | Sort-Object Name | ForEach-Object { ReadFile (Join-Path $_.FullName 'SKILL.md') }"
