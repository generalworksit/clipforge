@echo off
rem ClipForge launcher for Windows. Double-click it, or run: run.bat [--no-browser]
rem First run installs everything that is missing (Python, FFmpeg, Node.js, Python packages).
rem Later runs only start the app.
setlocal EnableExtensions
cd /d "%~dp0"
title ClipForge
rem --setup-only installs everything and exits without starting the app (for scripts and AI agents)
set "CF_SETUP_ONLY="
if /i "%~1"=="--setup-only" set CF_SETUP_ONLY=1

rem First start (no .venv yet): a short explanation window before the setup begins
if not exist ".venv\Scripts\python.exe" if not defined CF_SETUP_ONLY (
    powershell -NoProfile -ExecutionPolicy Bypass -File "tools\welcome.ps1" >nul 2>nul
)

rem ---------------------------------------------------------------- FFmpeg (required)
call :have ffmpeg ffprobe
if not errorlevel 1 goto :ffmpeg_ok
echo.
echo [setup] FFmpeg is missing, installing it ...
call :winget Gyan.FFmpeg
call :refresh_path
call :have ffmpeg ffprobe
if errorlevel 1 goto :no_ffmpeg
:ffmpeg_ok

rem ---------------------------------------------------------------- Python 3.11 to 3.13 (required)
if exist ".venv\Scripts\python.exe" goto :check_deps
call :find_python
if defined PY goto :make_venv
echo.
echo [setup] Python 3.12 is missing, installing it ...
call :winget Python.Python.3.12
call :refresh_path
call :find_python
if not defined PY goto :no_python
:make_venv
echo [setup] Creating the virtual environment in .venv with %PY% ...
%PY% -m venv .venv
if errorlevel 1 goto :no_python

rem ---------------------------------------------------------------- Python packages
:check_deps
".venv\Scripts\python.exe" -c "import fastapi, faster_whisper, yt_dlp, yaml, multipart, curl_cffi, kokoro_onnx, soundfile, cv2, PIL" >nul 2>nul
if not errorlevel 1 goto :live_deps
echo.
echo [setup] Installing Python packages, this takes a few minutes the first time ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_failed
where nvidia-smi >nul 2>nul
if errorlevel 1 goto :live_deps
echo [setup] NVIDIA GPU found, installing CUDA libraries for faster transcription ...
".venv\Scripts\python.exe" -m pip install -r requirements-cuda.txt
if errorlevel 1 goto :pip_failed

:live_deps
".venv\Scripts\python.exe" -c "import grpc, streamlink, google.protobuf" >nul 2>nul
if not errorlevel 1 goto :node
echo [setup] Installing Auto monitor packages ...
".venv\Scripts\python.exe" -m pip install -r requirements-live.txt
if errorlevel 1 goto :pip_failed

rem ---------------------------------------------------------------- Node.js (Auto monitor only)
:node
call :have node
if not errorlevel 1 goto :ai_choice
echo.
echo [setup] Node.js is missing, installing the LTS version for Auto monitor ...
call :winget OpenJS.NodeJS.LTS
call :refresh_path
call :have node
if not errorlevel 1 goto :ai_choice
echo [setup] Node.js could not be installed. Every tool works except Auto monitor.

rem ---------------------------------------------------------------- Ollama (optional, asked once)
:ai_choice
if defined CF_SETUP_ONLY goto :launch
call :have ollama
if not errorlevel 1 goto :launch
if exist ".venv\ollama-choice.txt" goto :launch
echo.
echo Optional: a local AI (Ollama + qwen3:8b, about 6 GB) picks better moments for clips.
echo Without it ClipForge still works and picks moments from the transcript.
choice /c YN /t 30 /d N /m "Install it now"
if errorlevel 2 (
    echo no> ".venv\ollama-choice.txt"
    goto :launch
)
call :winget Ollama.Ollama
call :refresh_path
echo yes> ".venv\ollama-choice.txt"
set CF_PULL_MODEL=1

rem ---------------------------------------------------------------- start
:launch
if defined CF_SETUP_ONLY goto :setup_done
call :ollama
rem Quick check that everything ClipForge needs is present (a few seconds)
".venv\Scripts\python.exe" -m app.health
if errorlevel 1 (
    pause
    exit /b 1
)
echo.
echo ClipForge is starting at http://127.0.0.1:8000  (close this window to stop it)
".venv\Scripts\python.exe" -m app %*
rem Stop the Ollama server this script started (closing the window stops it too).
if defined CF_STARTED_OLLAMA taskkill /im ollama.exe /t /f >nul 2>nul
goto :eof


:setup_done
echo.
".venv\Scripts\python.exe" -m app.health
echo.
echo Setup complete. Start ClipForge with run.bat, it opens http://127.0.0.1:8000
exit /b 0

rem ================================================================= helpers

:have
rem errorlevel 0 when every command given is on PATH
if "%~1"=="" exit /b 0
where %1 >nul 2>nul || exit /b 1
shift
goto :have

:find_python
rem Sets PY to a Python between 3.11 and 3.13. The Microsoft Store "python" stub fails the check.
set "PY="
for %%C in ("py -3.12" "py -3.11" "py -3.13" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul && set "PY=%%~C"
    )
)
exit /b 0

:winget
where winget >nul 2>nul
if errorlevel 1 (
    echo [setup] winget is not available on this PC, cannot install %1 automatically.
    exit /b 1
)
winget install --exact --id %1 --silent --accept-source-agreements --accept-package-agreements
exit /b 0

:refresh_path
rem winget changes PATH for new windows only; reload it so this window sees the new tools.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
exit /b 0

:ollama
rem Start Ollama when it is installed and not already running.
rem OLLAMA_VULKAN=0 makes it use the NVIDIA GPU, not the integrated one.
curl.exe -s -m 2 -o nul http://127.0.0.1:11434/api/tags >nul 2>nul
if not errorlevel 1 (
    echo Ollama is already running.
    goto :ollama_pull
)
where ollama >nul 2>nul
if errorlevel 1 (
    echo Ollama is not installed, clips are picked without the local AI. Get it at https://ollama.com
    exit /b 0
)
echo Starting Ollama ...
set OLLAMA_VULKAN=0
set CF_STARTED_OLLAMA=1
start "" /b cmd /c "ollama serve >> ollama.log 2>&1"
set /a CF_TRIES=0
:ollama_wait
curl.exe -s -m 2 -o nul http://127.0.0.1:11434/api/tags >nul 2>nul
if not errorlevel 1 (
    echo Ollama is ready.
    goto :ollama_pull
)
set /a CF_TRIES+=1
if %CF_TRIES% geq 20 (
    echo Ollama did not answer within 20 seconds, see ollama.log. ClipForge starts anyway.
    exit /b 0
)
ping -n 2 127.0.0.1 >nul
goto :ollama_wait
:ollama_pull
if not defined CF_PULL_MODEL exit /b 0
echo [setup] Downloading the qwen3:8b model, about 5 GB ...
ollama pull qwen3:8b
exit /b 0


rem ================================================================= failures

:no_ffmpeg
echo.
echo FFmpeg could not be installed automatically. Install it with:
echo     winget install Gyan.FFmpeg
echo or download it from https://www.gyan.dev/ffmpeg/builds/ and add its bin folder to PATH,
echo then run run.bat again.
if not defined CF_SETUP_ONLY pause
exit /b 1

:no_python
echo.
echo Python 3.11, 3.12 or 3.13 is required and could not be installed automatically.
echo Install it with:  winget install Python.Python.3.12
echo or from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run run.bat again.
if not defined CF_SETUP_ONLY pause
exit /b 1

:pip_failed
echo.
echo Package installation failed, see the messages above. Check your internet connection
echo and run run.bat again. To start clean, delete the .venv folder first.
if not defined CF_SETUP_ONLY pause
exit /b 1
