@echo off
rem Starts Studio with the project's own Python, from the project folder, and opens the browser.
rem Run it from a normal terminal or by double-clicking it (not from inside a Claude Code session).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Could not find .venv\Scripts\python.exe in %CD%
  pause
  exit /b 1
)
set "PATH=%CD%\.venv\Scripts;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin;%PATH%"
echo Studio is starting. Leave this window open; close it to stop Studio.
".venv\Scripts\python.exe" -m studio serve --open
pause
