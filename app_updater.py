"""User-triggered GitHub releases updater; versioned installs, no in-place overwrite."""
import hashlib,json,os,re,shutil,stat,subprocess,tempfile,threading,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit
from urllib.request import Request,urlopen

REPOSITORY='clemmylk/activtrack-report-generator'
RELEASES='https://github.com/'+REPOSITORY+'/releases'
API='https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
MAX_DOWNLOAD=300*1024*1024
MAX_EXPANDED=1500*1024*1024

def version_number(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value).strip())
    if not match:raise ValueError('The release must use a stable version such as v1.8.2.')
    return tuple(map(int,match.groups()))

def installed_version(base):
    text=(Path(base)/'VERSION.txt').read_text(encoding='utf-8-sig')
    match=re.search(r'(?im)^Version\s+(\d+\.\d+\.\d+)\s*$',text)
    if not match:raise ValueError('Application version information is missing.')
    return match[1]

def trusted_asset(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.netloc=='github.com' and p.path.startswith('/'+REPOSITORY+'/releases/download/') and not p.username and not p.password and not p.query

def open_download(url):
    if not trusted_asset(url):raise ValueError('Update download is not from the official GitHub repository.')
    response=urlopen(Request(url,headers={'User-Agent':'ActivTrack-Updater','Accept':'application/octet-stream'}),timeout=45)
    host=urlsplit(response.geturl()).hostname or ''
    if urlsplit(response.geturl()).scheme!='https' or host not in {'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','objects-origin.githubusercontent.com'}:
        response.close();raise ValueError('Unexpected update download destination.')
    return response

def select_release(release):
    if release.get('draft') or release.get('prerelease'):raise ValueError('Only published stable releases can be installed.')
    tag=str(release.get('tag_name',''));version='.'.join(map(str,version_number(tag)))
    name=f'ActivTrack-Report-Generator-{version}-Windows-x64.zip'
    assets={a.get('name'):a for a in release.get('assets',[])}
    if name not in assets or 'SHA256SUMS.txt' not in assets:raise ValueError('The latest release is missing its Windows ZIP or checksum file. Try again after publishing both assets.')
    archive,checksum=assets[name],assets['SHA256SUMS.txt']
    if not all(trusted_asset(a.get('browser_download_url','')) for a in (archive,checksum)):raise ValueError('Untrusted release assets.')
    if not 0<int(archive.get('size',0))<=MAX_DOWNLOAD:raise ValueError('Unexpected update package size.')
    return {'version':version,'name':name,'archive':archive,'checksum':checksum,'notes':str(release.get('body',''))[:8000],'url':RELEASES+'/tag/'+tag}

def read_checksum(text,name):
    matches=[]
    for line in text.splitlines():
        match=re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)',line.strip())
        if match and match[2].strip()==name:matches.append(match[1].lower())
    if len(matches)!=1:raise ValueError('A unique checksum for this package was not found.')
    return matches[0]

def extract_package(archive,destination,version):
    """Validate the whole archive before extracting any file."""
    root=f'ActivTrack-Report-Generator-{version}-Windows-x64'
    destination=Path(destination).resolve();seen=set()
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        if len(infos)>20000 or sum(i.file_size for i in infos)>MAX_EXPANDED:raise ValueError('Update archive is unexpectedly large.')
        for info in infos:
            name=info.orig_filename
            parts=PurePosixPath(name).parts
            if '\x00' in name or '\\' in name or not parts or parts[0]!=root or any(p in ('.','..') or ':' in p or p.endswith((' ','.')) for p in parts):raise ValueError('Unsafe path in update archive.')
            if stat.S_ISLNK(info.external_attr>>16):raise ValueError('Links are not allowed in update archives.')
            if any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p,re.I) for p in parts):raise ValueError('Unsafe Windows filename in update.')
            key='/'.join(parts).casefold()
            if key in seen:raise ValueError('Duplicate path in update archive.')
            seen.add(key)
            if not (destination/name).resolve().is_relative_to(destination):raise ValueError('Update path escapes installation folder.')
        required={'runtime/python.exe','runtime/pythonw.exe','polished_app.py','app_updater.py','02_START_APP.bat','web_ui/dist/index.html','VERSION.txt'}
        if not all((root+'/'+p).casefold() in seen for p in required):raise ValueError('The update package is incomplete.')
        z.extractall(destination)
    installed=destination/root
    if installed_version(installed)!=version:raise ValueError('Package version does not match the release.')
    return installed

def update_shortcuts(old,new):
    # Fixed PowerShell program; paths are separately quoted, never interpreted as commands.
    quote=lambda p:"'"+str(p).replace("'","''")+"'"
    code="$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; $desk=$shell.SpecialFolders('Desktop'); $changed=0; "
    code+='$old='+quote(Path(old).resolve())+'; $target='+quote(Path(new).resolve()/'02_START_APP.bat')+'; $work='+quote(Path(new).resolve())+'; '
    code+="foreach($file in Get-ChildItem -LiteralPath $desk -Filter '*.lnk'){ $link=$shell.CreateShortcut($file.FullName); if($link.TargetPath -and $link.TargetPath.StartsWith($old+'\\',[StringComparison]::OrdinalIgnoreCase)){ $link.TargetPath=$target; $link.Arguments=''; $link.WorkingDirectory=$work; $link.IconLocation=(Join-Path $work 'assets/activtrack-icon.ico'); $link.Save(); $changed++ } }; "
    code+="if($changed -eq 0){ $link=$shell.CreateShortcut((Join-Path $desk 'ActivTrack Report Generator.lnk')); $link.TargetPath=$target; $link.WorkingDirectory=$work; $link.IconLocation=(Join-Path $work 'assets/activtrack-icon.ico'); $link.Save() }"
    exe=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',code],check=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),capture_output=True)

class UpdateManager:
    def __init__(self,base,finished=lambda:None,home=None):
        self.base=Path(base);self.finished=finished;self.current=installed_version(base)
        self.home=Path(home) if home else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'ActivTrack Report Generator'/'updates'
        self.lock=threading.Lock();self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='app-update')
        self.release=None;self.installed=None
        self.state={'phase':'idle','busy':False,'current':self.current,'latest':'','progress':0,'message':'Check GitHub for the latest stable version.','releases_url':RELEASES}
    def snapshot(self):
        with self.lock:return dict(self.state)
    def set(self,**values):
        with self.lock:self.state.update(values)
    def submit(self,phase,fn):
        with self.lock:
            if self.state['busy']:return False
            self.state.update(busy=True,phase=phase,message='Checking GitHub…' if phase=='checking' else 'Preparing update…',progress=0)
        def work():
            try:fn()
            except Exception as exc:self.set(phase='error',message=str(exc))
            finally:self.set(busy=False)
        self.executor.submit(work);return True
    def check(self):return self.submit('checking',self._check)
    def _check(self):
        with urlopen(Request(API,headers={'User-Agent':'ActivTrack-Updater','Accept':'application/vnd.github+json'}),timeout=20) as r:release=json.loads(r.read(2_000_000))
        selected=select_release(release);self.release=selected
        available=version_number(selected['version'])>version_number(self.current)
        newer_local=version_number(self.current)>version_number(selected['version'])
        self.set(phase='available' if available else 'current',latest=selected['version'],notes=selected['notes'],message='A newer version is available.' if available else 'Your installed version is newer than the latest published release.' if newer_local else 'Your app is up to date.')
    def install(self):
        if not self.release or version_number(self.release['version'])<=version_number(self.current):raise ValueError('Check for a newer release before installing.')
        return self.submit('downloading',self._install)
    def _install(self):
        release=self.release;self.home.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='download-',dir=self.home) as folder:
            temp=Path(folder);archive=temp/release['name']
            with open_download(release['checksum']['browser_download_url']) as r:checksum=read_checksum(r.read(65537).decode('utf-8-sig'),release['name'])
            digest=hashlib.sha256();count=0;size=int(release['archive']['size'])
            with open_download(release['archive']['browser_download_url']) as r,archive.open('wb') as f:
                while chunk:=r.read(256*1024):
                    count+=len(chunk)
                    if count>MAX_DOWNLOAD or count>size:raise ValueError('Update download exceeds expected size.')
                    digest.update(chunk);f.write(chunk)
                    self.set(progress=round(75*count/size),message='Downloading version '+release['version']+'…')
            if count!=size or digest.hexdigest()!=checksum:raise ValueError('Update checksum failed. Nothing was installed; please download again.')
            self.set(phase='verifying',progress=80,message='Checking and unpacking the update…')
            extracted=extract_package(archive,temp/'extracted',release['version'])
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            subprocess.run([str(extracted/'runtime/python.exe'),'-I','-c','import sys,pathlib; sys.path.insert(0,str(pathlib.Path.cwd())); import tkinter,selenium,openpyxl,PIL,polished_app,app_updater; tkinter.Tcl()'],cwd=extracted,check=True,timeout=40,creationflags=flags,capture_output=True)
            target=self.home/'versions'/release['version']
            if target.exists():raise ValueError('This version already has an installation folder. The existing installation was left unchanged.')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(extracted,target)
            self.installed=target
            self.set(phase='ready',progress=100,path=str(target),message='Update installed. Restart when you are ready; the previous version is kept.')
    def activate(self,workspace):
        if not self.installed or self.snapshot()['phase'] not in ('ready','error'):raise ValueError('Install the update first.')
        def work():
            target=self.installed
            runtime=target/'.runtime';runtime.mkdir(exist_ok=True)
            (runtime/'reload-workspace.json').write_text(json.dumps(workspace),encoding='utf-8')
            update_shortcuts(self.base,target)
            script=target/'launch.py' if (target/'launch.py').is_file() else target/'polished_app.py'
            subprocess.Popen([str(target/'runtime/pythonw.exe'),str(script)],cwd=target,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.set(phase='restarting',message='The updated app is opening. You can close this old window.')
            self.finished()
        return self.submit('activating',work)
    def close(self):self.executor.shutdown(wait=False,cancel_futures=True)
