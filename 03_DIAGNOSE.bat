@echo off
cd /d "%~dp0"
"%~dp0runtime\python.exe" -c "import sys,tkinter,selenium,openpyxl,PIL; import tracking_config; print('Runtime OK:',sys.version); print('Carrier tools:',all(p.is_file() for p in tracking_config.CARRIERS.values())); print('Interface:',__import__('pathlib').Path('web_ui/dist/index.html').is_file())"
pause
