"""Portable entry point with a readable startup error on failure."""
import os,sys,runpy,traceback,ctypes
from pathlib import Path
base=Path(__file__).resolve().parent
os.chdir(base)
sys.path.insert(0,str(base))
try:
    runpy.run_path(str(base/'polished_app.py'),run_name='__main__')
except Exception:
    log=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'ActivTrack Report Generator'/'startup-error.log'
    log.parent.mkdir(parents=True,exist_ok=True)
    log.write_text(traceback.format_exc(),encoding='utf-8')
    ctypes.windll.user32.MessageBoxW(0,'The app could not start.\nError details: '+str(log),'ActivTrack Report Generator',16)
