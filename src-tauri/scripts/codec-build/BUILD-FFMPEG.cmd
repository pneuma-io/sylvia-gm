@echo off
setlocal
if "%~4"=="" (
  echo Usage: BUILD-FFMPEG.cmd PYTHON_EXE SH_EXE BASH_EXE MAKE_EXE
  exit /b 1
)
if not defined VCToolsInstallDir (
  echo Run from an existing x64 Visual Studio Developer Command Prompt.
  exit /b 1
)
set "ROOT=%~dp0.."
set "FFMPEG_BUILD_PYTHON=%~f1"
set "FFMPEG_BUILD_SH=%~s2"
set "FFMPEG_BUILD_MAKE=%~f4"
if exist "%ROOT%\ffmpeg-8.1.2\config.h" (
  echo Use a new extraction directory; existing build state is preserved.
  exit /b 1
)
tar -xf "%ROOT%\ffmpeg-8.1.2.tar.xz" -C "%ROOT%"
if errorlevel 1 exit /b 1
"%~3" "%~dp0configure.sh"
if errorlevel 1 exit /b 1
"%FFMPEG_BUILD_PYTHON%" "%~dp0repair-msvc-make.py" "%ROOT%"
if errorlevel 1 exit /b 1
"%~3" "%~dp0compile.sh"
exit /b %errorlevel%
