"""Paste-to-snapshot long-term schedule reconciliation (read only)."""
import json
import re
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from tracking_report import latest_booking_source, schedules_from_source
from automated_tracking import voyage_key
from tracking_config import BOT_ROOT
sys.path.insert(0, str(BOT_ROOT))
from carrier_bot_common import port_key, compact

DATE = re.compile(r'^\d{1,2}-[A-Za-z]{3}-\d{4}$')
def date_value(s):
    if not s: return ''
    for fmt in ('%d-%b-%Y','%Y-%m-%d'):
        try: return datetime.strptime(str(s)[:10] if fmt=='%Y-%m-%d' else str(s),fmt).date().isoformat()
        except ValueError: pass
    return ''

def parse_schedule(pasted):
    rows, issues = [], []
    blocks=re.split(r'(?=^(?:POL|POR)\s*:)',pasted,flags=re.M)
    for block in blocks:
        t=[v.strip().strip('|').strip() for line in block.splitlines() for v in line.split('\t') if v.strip().strip('|').strip()]
        if not t: continue
        route=t[0]
        pol=re.search(r'POL\s*:\s*(.*?)(?:\s*\(|$)',route,re.I)
        por=re.search(r'POR\s*:\s*(.*?)\s+POL\s*:',route,re.I)
        if not pol:
            issues.append(('Unrecognized route section',block[:300])); continue
        start=[]
        for i in range(1,len(t)-2):
            if not DATE.fullmatch(t[i-1]) and re.fullmatch(r'\d{1,2}',t[i]) and 1<=int(t[i])<=53 and re.search('[A-Za-z]',t[i+1]) and re.fullmatch(r'(?=[A-Za-z0-9/ -]*\d)[A-Za-z0-9/ -]+|TBA|TBN',t[i+2]) and not DATE.fullmatch(t[i+2]): start.append(i)
        if not start: issues.append(('No schedule rows found',route))
        for a,b in zip(start,start[1:]+[len(t)]):
            r=t[a:b]; dates=[(i,date_value(v)) for i,v in enumerate(r) if DATE.fullmatch(v)]
            base={'section':route,'week':r[0],'vessel':r[1],'voyage':r[2]}
            if dates:
                tail=r[dates[-1][0]+1:]
                labels=[v for v in tail if not re.fullmatch(r'\d+(?:\.\d+)?',v)]
                base['carrier']=labels[1] if len(labels)>1 else ''
            if 'TRANSSHIPMENT' not in route.upper():
                if len(dates)<2: issues.append(('ETD/ETA dates missing',str(r))); continue
                rows.append(dict(base,leg=1,pol=pol.group(1).strip(),pod='COLOMBO',etd=dates[-2][1],eta=dates[-1][1]))
            else:
                if len(dates)<5: issues.append(('Incomplete transshipment dates',str(r))); continue
                # Last five dates are origin ETD, TS ETA, TS cutoff, TS ETD, CMB ETA.
                d=dates[-5:]; connecting=r[d[1][0]+1:d[2][0]]
                if len(connecting)<3 or connecting[0].isdigit() is False:
                    issues.append(('Connecting vessel cannot be read safely',str(r))); continue
                rows.append(dict(base,leg=1,pol=por.group(1).strip() if por else '',pod=pol.group(1).strip(),etd=d[0][1],eta=d[1][1]))
                rows.append(dict(base,leg=2,vessel=connecting[1],voyage=connecting[2],pol=pol.group(1).strip(),pod='COLOMBO',etd=d[3][1],eta=d[4][1]))
    return rows,issues

def compare(rows,data):
    legs=[]
    for s in schedules_from_source(data):
        for l in s.get('transshipments') or []:
            legs.append(dict(vs=s.get('reference_number',''),vessel=(l.get('vessel') or {}).get('name',''),voyage=l.get('voyage',''),pol=(l.get('departure_port') or {}).get('name',''),pod=(l.get('arrival_port') or {}).get('name',''),etd=date_value(l.get('etd')),eta=date_value(l.get('eta')),leg=l.get('leg_number','')))
    results=[]
    for r in rows:
        candidates=[l for l in legs if compact(l['vessel'])==compact(r['vessel']) and voyage_key(l['voyage'])==voyage_key(r['voyage']) and port_key(l['pol'])==port_key(r['pol']) and port_key(l['pod'])==port_key(r['pod'])]
        if not candidates: results.append(dict(r,vs='',at_etd='',at_eta='',result='NOT IN CAPTURED JSON')); continue
        for l in candidates:
            changes=[]
            for field in ('etd','eta'):
                if not l[field]: changes.append(field.upper()+' MISSING IN ACTIVETRACK')
                elif r[field]!=l[field]: changes.append(field.upper()+(' DELAYED' if r[field]>l[field] else ' ADVANCED'))
            results.append(dict(r,vs=l['vs'],at_etd=l['etd'],at_eta=l['eta'],result='; '.join(changes) or 'NO CHANGE'))
    return results

def export_report(rows,issues,source,folder,pasted):
    folder=Path(folder)/'Long Term Tracking'; folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    output=folder/f'Long_Term_Comparison_{stamp}.xlsx'
    wb=Workbook(); wb.remove(wb.active)
    columns=[('vs','VESSEL ID'),('vessel','VESSEL'),('voyage','VOYAGE'),('leg','LEG'),('pol','POL'),('pod','POD'),('at_etd','ACTIVETRACK ETD'),('etd','LIVE CARRIER ETD'),('at_eta','ACTIVETRACK ETA'),('eta','LIVE CARRIER ETA'),('result','DETECTED CHANGES'),('manual','MANUAL REASON'),('pasted_etd','PASTED ETD'),('pasted_eta','PASTED ETA'),('source_url','CARRIER SOURCE'),('section','SOURCE ROUTE')]
    for title,selected in [('NEED CHANGES',[r for r in rows if r['result']!='NO CHANGE']),('NEED MANUAL CHECKING',[r for r in rows if r.get('manual')])]:
        ws=wb.create_sheet(title);ws.append(['Long Term Schedule vs ActiveTrack Snapshot']);ws.append(['Source: '+source.name]);ws.append([label for _,label in columns])
        for r in selected:
            ws.append([r.get(k,'') for k,_ in columns]);n=ws.max_row
            for i,(k,_) in enumerate(columns,1):
                cell=ws.cell(n,i);cell.alignment=Alignment(vertical='center',wrap_text=True)
                color='B91C1C' if r['result']=='NOT IN CAPTURED JSON' else '111827'; bold=r['result']=='NOT IN CAPTURED JSON'
                if k in ('etd','eta') and r.get(k) and r.get('at_'+k) and r[k]!=r['at_'+k]: color='B91C1C' if r[k]>r['at_'+k] else '15803D';bold=True
                cell.font=Font(name='Calibri',size=11,color=color,bold=bold)
                cell.fill=PatternFill('solid',fgColor='EFF6FF' if n%2==0 else 'FFFFFF')
            ws.row_dimensions[n].height=32
        ws.freeze_panes='C4';ws.auto_filter.ref=f'A3:P{max(3,ws.max_row)}';ws.sheet_view.showGridLines=False
        for c in ws[3]:c.fill=PatternFill('solid',fgColor='17365D');c.font=Font(color='FFFFFF',bold=True)
        for col in 'ABCDEFGHIJKLMNOP':ws.column_dimensions[col].width= {'A':16,'B':29,'C':18,'K':40,'L':65,'O':55,'P':65}.get(col,23)
    wb.save(output)
    (folder/f'Long_Term_Paste_{stamp}.txt').write_text(pasted,encoding='utf-8')
    (folder/f'Long_Term_Run_{stamp}.json').write_text(json.dumps({'source_path':str(source),'source_modified':source.stat().st_mtime,'rows':rows,'parse_issues':issues},indent=2),encoding='utf-8')
    return output

class LongTermWindow:
    def __init__(self,parent,output_getter,embedded=False,source_getter=None):
        self.source_getter=source_getter
        self.embedded=embedded;self.get_output=output_getter
        self.window=tk.Frame(parent,bg='#FFFFFF') if embedded else tk.Toplevel(parent)
        if embedded:self.window.pack(fill='both',expand=True)
        else:self.window.title('Long Term Auto');self.window.geometry('950x700')
        ttk.Label(self.window,text='Long Term Auto',font=('Segoe UI',20,'bold')).pack(anchor='w',padx=20,pady=12)
        self.executor=ThreadPoolExecutor(max_workers=1);self.stop=threading.Event();self.process=None;self.busy=False
        ttk.Label(self.window,text='Paste future sailings. Opens carrier websites one at a time, then compares live dates against the latest Auto Track JSON.').pack(anchor='w',padx=20)
        self.editor=tk.Text(self.window,wrap='none',font=('Consolas',10));self.editor.pack(fill='both',expand=True,padx=20,pady=12)
        self.status=tk.StringVar(value='Ready — paste your schedule.')
        ttk.Label(self.window,textvariable=self.status).pack(anchor='w',padx=20)
        self.progress=ttk.Progressbar(self.window,mode='indeterminate');self.progress.pack(fill='x',padx=20,pady=5)
        buttons=ttk.Frame(self.window);buttons.pack(fill='x',padx=20,pady=10)
        self.start_button=ttk.Button(buttons,text='Track Live & Create Excel',style='Blue.TButton',command=self.run);self.start_button.pack(side='right')
        ttk.Button(buttons,text='Stop',command=self.cancel).pack(side='right',padx=8)
        if not embedded:self.window.protocol('WM_DELETE_WINDOW',self.close)
        from desktop_ui import lighten_widgets
        lighten_widgets(self.window)
    def cancel(self):
        self.stop.set()
        if self.process and self.process.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:self.process.terminate()
        self.status.set('Stopping. Remaining undetected sailings will be included for manual checking.')
    def close(self):
        if self.busy:
            self.cancel();return
        self.executor.shutdown(wait=False);self.window.destroy()
    def log(self,line):
        try:self.window.after(0,lambda:self.status.set(line[-180:]))
        except (RuntimeError,tk.TclError):pass
    def run(self):
        if self.busy:return
        if not getattr(self,'can_start',lambda:True)():
            self.status.set('Another tracking operation is running. Let it finish or stop it first.')
            return
        try:
            selected_source=self.source_getter() if self.source_getter else None
            source=Path(selected_source) if selected_source else latest_booking_source();pasted=self.editor.get('1.0','end');folder=self.get_output()
            rows,issues=parse_schedule(pasted)
            if not rows:raise ValueError('No schedule rows could be read. Include the complete route headings and columns.')
            data=json.loads(source.read_text(encoding='utf-8-sig'))
            self.busy=True;self.stop.clear();self.start_button.config(state='disabled');self.progress.start()
            def work():
                from long_term_live import collect,reconcile
                run_dir=Path(folder)/'Long Term Tracking'/'Run Data'/datetime.now().strftime('%Y%m%d_%H%M%S');run_dir.mkdir(parents=True,exist_ok=True)
                (run_dir/'locked_source.json').write_text(json.dumps(data),encoding='utf-8')
                calls,status=collect(rows,source,run_dir,self.stop,self.log,lambda p:setattr(self,'process',p))
                result=reconcile(compare(rows,data),calls,status)
                for issue,raw in issues:result.append(dict(vessel='UNREAD PASTED ROW',result='NO CHANGE',manual=issue,section=raw))
                path=export_report(result,issues,source,folder,pasted)
                return path,sum(r['result']!='NO CHANGE' for r in result),sum(bool(r.get('manual')) for r in result)
            future=self.executor.submit(work)
            def poll():
                if not future.done():self.window.after(200,poll);return
                self.busy=False;self.start_button.config(state='normal');self.progress.stop()
                try:
                    path,changed,manual=future.result();self.status.set(f'Complete: {changed} changed rows; {manual} manual checks. Source: {source.name}');os.startfile(path)
                except Exception as exc:messagebox.showerror('Long Term Auto',str(exc),parent=self.window)
            self.window.after(200,poll)
        except Exception as exc:messagebox.showerror('Long Term Auto',str(exc),parent=self.window)
