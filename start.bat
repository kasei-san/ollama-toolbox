@echo off
REM ---------------------------------------------------------------------------
REM  Local LLM + web search launcher.
REM
REM    start.bat                 -> interactive mode
REM    start.bat "your question" -> one-shot
REM    start.bat -f "..."        -> force a search (do not let the model decide)
REM
REM  Brings up Ollama and SearXNG if they are not already running, waits until
REM  both answer, then hands over to ollama-search.py.
REM
REM  NOTE: this file is deliberately pure ASCII.  cmd.exe reads .bat as the OEM
REM  code page (cp932 here), so non-ASCII text in the source would be mangled.
REM  Japanese output is left to the Python side, which handles UTF-8 properly.
REM  See ~/.claude/docs/windows-shell.md
REM ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul

set "HERE=%~dp0"
set "SEARXNG_DIR=E:\llm\searxng"
set "OLLAMA_URL=http://localhost:11434/api/tags"
set "SEARXNG_URL=http://127.0.0.1:8888/"

REM --- 1/2  Ollama -----------------------------------------------------------
curl -s -m 3 -o nul "%OLLAMA_URL%"
if not errorlevel 1 (
    echo [1/2] Ollama    : already running
    goto :searxng
)
echo [1/2] Ollama    : starting...
REM  Normally this comes from the user environment variable, but if this .bat is
REM  launched from a shell that predates it being set, Ollama silently falls back
REM  to its default model dir and "ollama list" comes up EMPTY -- every request
REM  then 404s with "model not found".  Pin it so the launcher is self-contained.
if not defined OLLAMA_MODELS set "OLLAMA_MODELS=E:\llm\ollama"
REM  %LOCALAPPDATA% on purpose: the home path is Japanese and gets mangled when
REM  passed literally.  See ~/.claude/docs/windows-shell.md
start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
call :waitfor "%OLLAMA_URL%" 30 Ollama || goto :fail

:searxng
REM --- 2/2  SearXNG ----------------------------------------------------------
curl -s -m 3 -o nul "%SEARXNG_URL%"
if not errorlevel 1 (
    echo [2/2] SearXNG   : already running
    goto :run
)
if not exist "%SEARXNG_DIR%\.venv\Scripts\python.exe" (
    echo ERROR: SearXNG venv not found at %SEARXNG_DIR%
    echo        See ~/.claude/docs/local-llm.md for the setup.
    goto :fail
)
echo [2/2] SearXNG   : starting...
set "SEARXNG_SETTINGS_PATH=%SEARXNG_DIR%\settings-local.yml"
REM  Two easy-to-miss things here:
REM   * /MIN must come BEFORE the title.  'start "title" /MIN prog' makes cmd
REM     treat the title as the command and fail with 'file \title\ not found'.
REM   * /D is required.  'python -m searx.webapp' only resolves if the working
REM     directory is the SearXNG checkout; without it the child dies instantly
REM     on ModuleNotFoundError and the wait loop just spins.
start /MIN /D "%SEARXNG_DIR%" "SearXNG" "%SEARXNG_DIR%\.venv\Scripts\python.exe" -m searx.webapp
REM first boot loads every engine, so allow generous time
call :waitfor "%SEARXNG_URL%" 90 SearXNG || goto :fail

:run
echo.
python "%HERE%ollama-search.py" %*
exit /b %errorlevel%

REM --- helper: poll a URL until it answers -----------------------------------
REM   %~1 url   %~2 max seconds   %~3 label
:waitfor
set /a _left=%~2
:waitloop
curl -s -m 2 -o nul "%~1"
if not errorlevel 1 (
    echo       %~3 : ready
    exit /b 0
)
set /a _left-=1
if %_left% leq 0 (
    echo ERROR: %~3 did not come up within %~2s
    exit /b 1
)
REM  Sleep ~1s.  timeout.exe is avoided on purpose: it dies with "Input
REM  redirection is not supported" whenever stdin is redirected, and a bare
REM  "timeout" resolves to GNU coreutils' timeout when this .bat is launched
REM  from Git Bash.  ping has neither problem.
ping -n 2 127.0.0.1 >nul 2>&1
goto :waitloop

:fail
echo.
echo Startup failed.  Nothing was launched.
pause
exit /b 1
