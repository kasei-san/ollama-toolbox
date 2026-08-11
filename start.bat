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
REM  Non-ASCII output is left to the Python side, which handles UTF-8 properly.
REM ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul

set "HERE=%~dp0"
REM  Defaults assume SearXNG sits next to this checkout:
REM      <parent>\searxng
REM      <parent>\ollama-search   <- you are here
REM  Override with the SEARXNG_DIR / SEARXNG_URL / OLLAMA_HOST env vars.
if not defined SEARXNG_DIR set "SEARXNG_DIR=%HERE%..\searxng"
if not defined SEARXNG_URL set "SEARXNG_URL=http://127.0.0.1:8888/"
if not defined OLLAMA_HOST set "OLLAMA_HOST=http://localhost:11434"
set "OLLAMA_URL=%OLLAMA_HOST%/api/tags"

REM --- 1/2  Ollama -----------------------------------------------------------
curl -s -m 3 -o nul "%OLLAMA_URL%"
if not errorlevel 1 (
    echo [1/2] Ollama    : already running
    goto :searxng
)
echo [1/2] Ollama    : starting...
REM  If you keep your models outside Ollama's default location, set OLLAMA_MODELS
REM  as a user environment variable.  Watch out: when this .bat is launched from a
REM  shell that predates that variable being set, Ollama silently falls back to its
REM  default model dir, "ollama list" comes up EMPTY, and every request then 404s
REM  with "model not found".
REM  %LOCALAPPDATA% is used on purpose rather than a literal path: a non-ASCII
REM  user name gets mangled when passed literally through some shells.
start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
call :waitfor "%OLLAMA_URL%" 30 Ollama || goto :fail

:searxng
REM --- 2/2  SearXNG ----------------------------------------------------------
REM  Only the "searxng" backend needs a separate process.  The default (ddgs)
REM  reaches the search engines from inside the Python process, so skip all this.
if not defined SEARCH_BACKEND set "SEARCH_BACKEND=ddgs"
if /i not "%SEARCH_BACKEND%"=="searxng" (
    echo [2/2] SearXNG   : not needed ^(SEARCH_BACKEND=%SEARCH_BACKEND%^)
    goto :run
)
curl -s -m 3 -o nul "%SEARXNG_URL%"
if not errorlevel 1 (
    echo [2/2] SearXNG   : already running
    goto :run
)
if not exist "%SEARXNG_DIR%\.venv\Scripts\python.exe" (
    echo ERROR: SearXNG venv not found at %SEARXNG_DIR%
    echo        See README.md for the setup.
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
REM  Prefer this repo's venv (it has ddgs); fall back to whatever python is on PATH.
set "PY=python"
if exist "%HERE%.venv\Scripts\python.exe" set "PY=%HERE%.venv\Scripts\python.exe"
"%PY%" "%HERE%ollama-search.py" %*
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
