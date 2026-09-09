@echo off
REM templates/hooks/claude-code/session-start.cmd — Windows shim for the
REM deterministic SessionStart hook (fleet-deterministic-seams Chunk 6).
REM Runs the bash version via Git Bash. NOTE: checks for Git Bash
REM explicitly (not WSL bash), matching hooks\session-start.cmd's existing
REM precedent -- WSL bash cannot resolve Windows drive-letter paths, and
REM its exit codes do not propagate correctly through cmd.exe.

set "HOOK_DIR=%~dp0"

set "GIT_BASH="
if exist "C:\Program Files\Git\bin\bash.exe" set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"
if "%GIT_BASH%"=="" if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "GIT_BASH=C:\Program Files (x86)\Git\bin\bash.exe"

if not "%GIT_BASH%"=="" (
  "%GIT_BASH%" "%HOOK_DIR%session-start"
  exit /b 0
)

REM No Git Bash found: nothing to fall back to for this hook -- unlike the
REM prompt-caching hooks\session-start.cmd, there is no PowerShell-native
REM equivalent of `python -m scripts.fleet.cli` worth hand-rolling here.
REM Best-effort by construction (NFR-1): exit 0 regardless.
exit /b 0
