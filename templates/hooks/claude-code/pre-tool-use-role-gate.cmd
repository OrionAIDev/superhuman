@echo off
REM templates/hooks/claude-code/pre-tool-use-role-gate.cmd — Windows shim
REM for the deterministic PreToolUse role gate (fleet-deterministic-seams
REM Chunk 7a). Runs the bash version via Git Bash. NOTE: checks for Git
REM Bash explicitly (not WSL bash), matching hooks\session-start.cmd's and
REM hooks\subagent-start.cmd's existing precedent -- WSL bash cannot
REM resolve Windows drive-letter paths, and its exit codes do not
REM propagate correctly through cmd.exe.

set "HOOK_DIR=%~dp0"

set "GIT_BASH="
if exist "C:\Program Files\Git\bin\bash.exe" set "GIT_BASH=C:\Program Files\Git\bin\bash.exe"
if "%GIT_BASH%"=="" if exist "C:\Program Files (x86)\Git\bin\bash.exe" set "GIT_BASH=C:\Program Files (x86)\Git\bin\bash.exe"

if not "%GIT_BASH%"=="" (
  "%GIT_BASH%" "%HOOK_DIR%pre-tool-use-role-gate"
  exit /b 0
)

REM No Git Bash found: nothing to fall back to for this hook -- same
REM posture as subagent-start.cmd. Best-effort by construction (NFR-9):
REM exit 0 regardless -- this hook is verify-only and must never become
REM the reason a dispatch is blocked.
exit /b 0
