"""Local React desktop shell; existing Python engines remain authoritative."""
import json, os, secrets, subprocess, threading, time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import tkinter as tk
from main import ReportApp

BASE=Path(__file__).parent
DIST=BASE/'web_ui'/'dist'
SESSION=BASE/'.runtime'/'desktop-session.json'

class DesktopBridge:
    def __init__(self,root,app):
        self.root=root;self.app=app;self.token=secrets.token_urlsafe(32)
        self.state={};self.lock=threading.Lock();self.last_seen=time.monotonic();self.started=False
        self.error='';self.cache_key=None;self.cache=[]
        self.latest_source=None
        original_save=app._save_source_json
        def save_source(*args,**kwargs):
            path=original_save(*args,**kwargs);self.latest_source=str(path);return path
        app._save_source_json=save_source
        from tkinter import messagebox
        messagebox.showinfo=lambda title,message,**kw:self.inform(title,message)
        messagebox.showerror=lambda title,message,**kw:self.inform(title,message,True)
        self.root.after(100,self.snapshot)
    def inform(self,title,message,error=False):
        if error:self.error=str(message)
        self.app._log(str(title)+': '+str(message).replace('\n',' '))
        return 'ok'
    def snapshot(self):
        a=self.app
        try:
            runs={}
            for key,r in [('auto',a.automated_tracking_window),('manual',a.tracking_window),('longterm',a.long_term_window)]:
                if r and r.window.winfo_exists():
                    v=getattr(r,'status_var',getattr(r,'status',None));p=getattr(r,'progress',None)
                    runs[key]={'busy':r.busy,'status':v.get() if v else '', 'progress':float(p['value']) if p else 0,'maximum':float(p['maximum']) if p else 100}
            state={'busy':a.busy,'status':a.status_var.get(),'logs':a.log_box.get('1.0','end').strip().splitlines()[-10:],
                   'source':str(a.morning_source_path or ''),'reportSource':self.latest_source,'output':str(a.last_output or ''),'folder':a.output_var.get(),'date':a.as_of_var.get(),
                   'runs':runs,'error':self.error,'fcl':a.fcl_controller.snapshot(),'fclBusy':a.fcl_controller.busy}
            with self.lock:self.state=state
        except tk.TclError:return
        if self.started and time.monotonic()-self.last_seen>900 and not a.busy and not any(r.get('busy') for r in self.state.get('runs',{}).values()) and not a.fcl_controller.busy:
            a._on_close();return
        self.root.after(800,self.snapshot)
    def action(self,name,args):
        a=self.app;self.error=''
        try:
            if 'date' in args:
                datetime.strptime(args['date'],'%Y-%m-%d');a.as_of_var.set(args['date'])
            if 'save' in args:a.save_json_var.set(bool(args['save']))
            if name=='open':a._open_activtrack()
            elif name=='capture':a._generate_live()
            elif name=='all':a._generate_all_vessels()
            elif name=='folder':a._open_output_folder()
            elif name=='output':
                if a.last_output and a.last_output.is_file():os.startfile(a.last_output)
                else:raise ValueError('No report has been generated in this session.')
            elif name in ('import','browse','alerts'):
                self.root.attributes('-topmost',True)
                try:
                    if name=='import':a._import_json()
                    elif name=='browse':a._browse_output()
                    else:a.arrival_alerts.open()
                finally:self.root.attributes('-topmost',False)
            elif name=='auto':a._open_automated_tracking()
            elif name=='manual':
                from tracking_report import parse_vessel_names
                a.ui.tracking_mode('manual');names=parse_vessel_names(args.get('vessels',''))
                if not names:raise ValueError('Paste at least one vessel name.')
                from tracking_config import CARRIERS
                if args.get('carrier') not in CARRIERS:raise ValueError('Select a supported carrier.')
                a.tracking_window._start_tracking(args['carrier'],names)
            elif name=='longterm':
                a.ui.tracking_mode('longterm');r=a.long_term_window
                r.editor.delete('1.0','end');r.editor.insert('1.0',args.get('schedule',''));r.run()
            elif name=='stop':
                if a.tracking_window and a.tracking_window.busy:a.tracking_window.stop_tracking()
                if a.automated_tracking_window and a.automated_tracking_window.busy:a.automated_tracking_window._stop_tracking()
                if a.long_term_window and a.long_term_window.busy:a.long_term_window.cancel()
                a.fcl_controller.stop.set()
            elif name=='dashboard':a._open_dashboard()
            elif name=='check':a.fcl_controller.start()
            elif name=='export':a._open_dashboard();a.dashboard.export()
            else:raise ValueError('Unknown action')
        except Exception as exc:self.error=str(exc);a._log('Action needs attention: '+str(exc))
    def reports(self):
        with self.lock:state=dict(self.state)
        selected=state.get('reportSource') or state.get('source')
        path=Path(selected) if selected else None
        if not path:
            files=list(Path(state.get('folder') or Path.home()/'Documents'/'ActivTrack Reports').glob('ActivTrack_Vessel_Booking_Report_*_Source.json'))
            path=max(files,key=lambda p:p.stat().st_mtime) if files else None
        if not path or not path.is_file():return {'rows':[],'source':''}
        key=(str(path),path.stat().st_mtime_ns,state.get('date'))
        if self.cache_key!=key:
            from report_builder import normalize_source,_build_records
            from console_rules import booking_class
            data=json.loads(path.read_text(encoding='utf-8-sig'));n=normalize_source(data);details,legs,_=_build_records(n)
            raw={b.get('reference_number'):b for b in data['bookings']}
            rows=[]
            for r in details:
                b=raw.get(r['booking_ref'],{})
                rows.append({**r,'classification':booking_class(b),'agent':b.get('agent_name') or '', 'hbl':b.get('hbl_number') or '', 'mbl':b.get('mbl_number') or '', 'container':b.get('container_number') or ''})
            self.cache={'rows':rows,'legs':legs,'source':path.name,'previous':not bool(state.get('source'))};self.cache_key=key
        return self.cache

def make_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def send(self,data,status=200,kind='application/json'):
            body=json.dumps(data,default=str).encode() if kind=='application/json' else data
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
        def authorised(self):return secrets.compare_digest(self.headers.get('X-ActivTrack-Token',''),bridge.token)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path.startswith('/api/'):
                if not self.authorised():self.send({'error':'Unauthorised'},403);return
                bridge.last_seen=time.monotonic();bridge.started=True
                try:
                    if path=='/api/state':
                        with bridge.lock:data=dict(bridge.state)
                    elif path=='/api/reports':data=bridge.reports()
                    elif path=='/api/vessels':
                        from operations_dashboard import VESSEL_STATE
                        data=json.loads(VESSEL_STATE.read_text(encoding='utf-8')) if VESSEL_STATE.is_file() else {'rows':[]}
                    elif path=='/api/headers':
                        from notice_tools import HEADERS
                        data=HEADERS
                    else:self.send({},404);return
                    self.send(data)
                except Exception as exc:self.send({'error':str(exc)},500)
                return
            file=(DIST/(path.lstrip('/') or 'index.html')).resolve()
            if not file.is_relative_to(DIST.resolve()) or not file.is_file():self.send({},404);return
            import mimetypes
            self.send(file.read_bytes(),kind=mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
        def do_POST(self):
            if not self.authorised():self.send({'error':'Unauthorised'},403);return
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{self.server.server_port}'):
                self.send({'error':'Invalid origin'},403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size>2_000_000:raise ValueError('Input too large')
                data=json.loads(self.rfile.read(size))
                if self.path=='/api/notice':
                    from notice_tools import build_notice
                    row=dict(data.get('row') or {})
                    if row.get('vs_id') and not row.get('total_legs'):
                        with bridge.lock:source_path=bridge.state.get('source') or bridge.state.get('reportSource')
                        if not source_path:
                            try:
                                from tracking_report import latest_booking_source
                                source_path=latest_booking_source()
                            except FileNotFoundError:pass
                        if source_path:
                            from report_builder import normalize_source
                            schedules=normalize_source(json.loads(Path(source_path).read_text(encoding='utf-8-sig')))['schedules']
                            match=next((s for s in schedules if s['reference_number']==row['vs_id']),None)
                            if match:row['total_legs']=len(match['legs'])
                    self.send(build_notice(row,data.get('reason','')));return
                if self.path!='/api/action':self.send({},404);return
                bridge.root.after(0,lambda:bridge.action(data.get('name'),data.get('args') or {}))
                self.send({'queued':True})
            except Exception as exc:self.send({'error':str(exc)},400)
    return Handler

def main():
    # Reuse the running engine on a second shortcut click.
    try:
        from urllib.request import Request,urlopen
        current=json.loads(SESSION.read_text(encoding='utf-8'))
        if urlsplit(current['base']).hostname!='127.0.0.1':raise ValueError('Invalid desktop session')
        with urlopen(Request(current['base']+'/api/state',headers={'X-ActivTrack-Token':current['token']}),timeout=2) as response:
            if response.status==200:
                launch_window(current['url']);return
    except (OSError,ValueError,KeyError):pass
    root=tk.Tk();root.withdraw();app=ReportApp(root);root.withdraw()
    bridge=DesktopBridge(root,app)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(bridge))
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/?token={bridge.token}'
    SESSION.parent.mkdir(exist_ok=True)
    SESSION.write_text(json.dumps({'base':f'http://127.0.0.1:{server.server_port}','url':url,'token':bridge.token}),encoding='utf-8')
    launch_window(url)
    try:root.mainloop()
    finally:server.shutdown()

def launch_window(url):
    edge=Path(os.environ.get('PROGRAMFILES(X86)',r'C:\Program Files (x86)'))/'Microsoft'/'Edge'/'Application'/'msedge.exe'
    if not edge.is_file():edge=Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Microsoft'/'Edge'/'Application'/'msedge.exe'
    if edge.is_file():subprocess.Popen([str(edge),f'--app={url}','--start-maximized'],creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import webbrowser
        webbrowser.open(url)

if __name__=='__main__':main()
