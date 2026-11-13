@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\pythonw.exe" (
 echo Extract the entire ZIP first. The bundled runtime folder is missing.
 pause
 exit /b 1
)
start "" "%~dp0runtime\pythonw.exe" "%~dp0launch.py"
