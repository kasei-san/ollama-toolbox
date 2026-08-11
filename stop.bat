@echo off
REM ---------------------------------------------------------------------------
REM  Shut down what start.bat left running.
REM
REM    stop.bat        -> free the VRAM and stop SearXNG (keep Ollama resident)
REM    stop.bat /all   -> the above, plus quit Ollama entirely
REM
REM  Why this exists: start.bat launches both services detached, so they survive
REM  the launcher.  That is deliberate (the next run is then instant), but the
REM  model keeps ~15.8GB of VRAM for about 5 minutes after the last request.
REM  Before switching to Forge or ComfyUI you want that back immediately.
REM
REM  Pure ASCII on purpose - cmd reads .bat as the OEM code page (cp932 here).
REM ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion

REM --- 1  unload the model from VRAM ------------------------------------------
where ollama >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=1" %%m in ('ollama ps 2^>nul ^| findstr /v /c:"NAME"') do (
        echo unloading %%m
        ollama stop %%m >nul 2>&1
    )
)
echo VRAM     : released

REM --- 2  stop SearXNG --------------------------------------------------------
REM  Kill by listening port, never by image name: other python processes
REM  (ComfyUI, scripts) must not be caught in the blast.
set "SXPID="
for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /r /c:"LISTENING" ^| findstr /c:":8888 "') do set "SXPID=%%p"
if defined SXPID (
    taskkill /PID %SXPID% /T /F >nul 2>&1
    echo SearXNG  : stopped ^(pid %SXPID%^)
) else (
    echo SearXNG  : not running
)

REM --- 3  optionally quit Ollama ----------------------------------------------
if /i "%~1"=="/all" (
    taskkill /IM "ollama app.exe" /F >nul 2>&1
    taskkill /IM "ollama.exe" /F >nul 2>&1
    echo Ollama   : stopped
) else (
    echo Ollama   : left running ^(use "stop.bat /all" to quit it too^)
)
