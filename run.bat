@echo off
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 goto :no_ffmpeg
where ffprobe >nul 2>nul
if errorlevel 1 goto :no_ffmpeg

if exist ".venv\Scripts\python.exe" goto :check_deps
echo Creating virtual environment in .venv ...
python -m venv .venv
if errorlevel 1 goto :no_python

:check_deps
".venv\Scripts\python.exe" -c "import fastapi, faster_whisper, yt_dlp, yaml, multipart, curl_cffi, kokoro_onnx, soundfile, cv2, PIL" >nul 2>nul
if not errorlevel 1 goto :start
echo Installing Python packages, this takes a few minutes the first time ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_failed
where nvidia-smi >nul 2>nul
if errorlevel 1 goto :start
echo NVIDIA GPU found, installing CUDA libraries for faster-whisper ...
".venv\Scripts\python.exe" -m pip install -r requirements-cuda.txt
if errorlevel 1 goto :pip_failed

:start
".venv\Scripts\python.exe" -c "import grpc, streamlink, google.protobuf" >nul 2>nul
if not errorlevel 1 goto :monitor_ready
echo Installing background monitor dependencies ...
".venv\Scripts\python.exe" -m pip install -r requirements-live.txt
if errorlevel 1 goto :pip_failed
:monitor_ready
where node >nul 2>nul
if not errorlevel 1 goto :launch
echo Auto monitor needs Node.js 22 or newer. Other tools can still be used.
echo Install Node.js LTS and restart ClipForge to enable background detection.
:launch
call :ollama
".venv\Scripts\python.exe" -m app %*
rem Stop the Ollama server this script started (closing the window stops it too).
if defined CF_STARTED_OLLAMA taskkill /im ollama.exe /t /f >nul 2>nul
goto :eof

:ollama
rem The local AI (Ollama) makes moment picking much smarter. Start it when it is installed
rem and not already running. OLLAMA_VULKAN=0 makes it use the NVIDIA GPU, not the Intel one.
curl.exe -s -m 2 -o nul http://127.0.0.1:11434/api/tags >nul 2>nul
if not errorlevel 1 (
    echo Ollama is already running.
    exit /b 0
)
where ollama >nul 2>nul
if errorlevel 1 (
    echo Ollama is not installed, clips will be picked without the AI. Get it at https://ollama.com
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
    exit /b 0
)
set /a CF_TRIES+=1
if %CF_TRIES% geq 20 (
    echo Ollama did not answer within 20 seconds, see ollama.log. ClipForge starts anyway.
    exit /b 0
)
ping -n 2 127.0.0.1 >nul
goto :ollama_wait

:no_ffmpeg
echo ffmpeg and ffprobe are required. Install them with:
echo     winget install Gyan.FFmpeg
echo then open a new terminal and run run.bat again.
pause
exit /b 1

:no_python
echo Python 3.11 or newer is required. Install it with:
echo     winget install Python.Python.3.12
pause
exit /b 1

:pip_failed
echo Package installation failed, see the messages above.
pause
exit /b 1
