@echo off
REM ---------------------------------------------------------------------------
REM  Web UI for the local LLM + web search agent.
REM
REM    webui.bat            -> start the server and open a browser
REM    webui.bat --port N   -> use a specific port
REM
REM  Brings Ollama up if it is not already running, then serves the UI on
REM  127.0.0.1.  Same agent as start.bat -- this only adds a screen.
REM
REM  NOTE: pure ASCII on purpose.  cmd.exe reads .bat as the OEM code page
REM  (cp932 here), so non-ASCII source text gets mangled.  Japanese output is
REM  left to the Python side, which handles UTF-8 properly.
REM ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul

set "HERE=%~dp0"
if not defined OLLAMA_HOST set "OLLAMA_HOST=http://localhost:11434"
if not defined WEBUI_PORT set "WEBUI_PORT=4645"
set "OLLAMA_URL=%OLLAMA_HOST%/api/tags"

REM --- Ollama ----------------------------------------------------------------
curl -s -m 3 -o nul "%OLLAMA_URL%"
if not errorlevel 1 (
    echo [1/2] Ollama    : already running
    goto :run
)
echo [1/2] Ollama    : starting...
REM  %LOCALAPPDATA% rather than a literal path: a non-ASCII user name gets
REM  mangled when passed literally through some shells.
start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
set /a _left=30
:waitloop
curl -s -m 2 -o nul "%OLLAMA_URL%"
if not errorlevel 1 goto :ready
set /a _left-=1
if %_left% leq 0 (
    echo ERROR: Ollama did not come up within 30s
    pause
    exit /b 1
)
REM  ping, not timeout.exe: timeout dies with "Input redirection is not
REM  supported" when stdin is redirected, and a bare "timeout" resolves to GNU
REM  coreutils' timeout when this .bat is launched from Git Bash.
ping -n 2 127.0.0.1 >nul 2>&1
goto :waitloop
:ready
echo       Ollama    : ready

:run
echo [2/2] Web UI    : http://127.0.0.1:%WEBUI_PORT%
REM  Give the server a moment before the browser asks for the page.  If the
REM  port was taken, webui.py moves up and prints the real one to the console --
REM  the browser will land on a dead port and you just reload from there.
start "" "http://127.0.0.1:%WEBUI_PORT%"
set "PY=python"
if exist "%HERE%.venv\Scripts\python.exe" set "PY=%HERE%.venv\Scripts\python.exe"
"%PY%" "%HERE%webui.py" %*
exit /b %errorlevel%
