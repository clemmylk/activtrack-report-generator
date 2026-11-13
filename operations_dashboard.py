"""Full-screen operations view: FCL checks and console-first schedule changes."""
import json
import os
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime
from pathlib import Path
from fcl_tracking import COLOMBO, REPORTS, STATE, next_check, add_fcl_sheet,write_json
from container_browser import SITES

VESSEL_STATE=REPORTS/'FCL Tracking'/'vessel_dashboard.json'

def short_port(name,code=''):
    code=str(code or '').upper().strip()
    if len(code)==5 and code[:2].isalpha():return code[2:]
    city=str(name or '').split(',')[0].strip().upper()
    known={'COLOMBO':'CMB','HONG KONG':'HKG','SHANGHAI':'SHA','NINGBO':'NGB','SHENZHEN':'SZX','SHEKOU':'SHK','QINGDAO':'TAO','KAOHSIUNG':'KHH','KEELUNG':'KEL','SINGAPORE':'SIN','XIAMEN':'XMN','FUZHOU':'FOC','DALIAN':'DLC','LAEM CHABANG':'LCH','NHAVA SHEVA':'NSA','CHENNAI':'MAA','TUTICORIN':'TUT','FELIXSTOWE':'FXT','LONDON GATEWAY PORT':'LGP','HO CHI MINH':'SGN','MUNDRA':'MUN'}
    return known.get(city,city[:16] or '—')

def short_date(value):
    try:return datetime.fromisoformat(str(value)[:10]).strftime('%d %b %y')
    except (ValueError,TypeError):return str(value or '—')

def bootstrap_vessel_results():
    """Read the latest existing Auto Track workbook; never seed demonstration data."""
    from tracking_config import CARRIER_REPORTS_DIR
    files=list(CARRIER_REPORTS_DIR.glob('Automated_Daily_Vessel_Tracking_*.xlsx'))
    if not files:return
    latest=max(files,key=lambda p:p.stat().st_mtime)
    try:
        previous=json.loads(VESSEL_STATE.read_text(encoding='utf-8'))
        if Path(previous.get('output','')).is_file() and Path(previous['output']).stat().st_mtime>=latest.stat().st_mtime:return
    except (OSError,ValueError):pass
    from automated_tracking import HEADERS
    from openpyxl import load_workbook
    workbook=load_workbook(latest,read_only=True,data_only=True)
    try:
        sheet=workbook['ALL RESULTS'];labels=[c.value for c in sheet[4]];keys={label:key for key,label in HEADERS};rows=[]
        for cells in sheet.iter_rows(min_row=5,values_only=True):
            row={keys[label]:(value.isoformat()[:10] if hasattr(value,'isoformat') else value) for label,value in zip(labels,cells) if label in keys}
            if row.get('vs_id'):rows.append(row)
        write_json(VESSEL_STATE,{'rows':rows,'output':str(latest),'checked_at':'Report generated '+datetime.fromtimestamp(latest.stat().st_mtime,COLOMBO).isoformat(timespec='seconds')})
    finally:workbook.close()

def console_rank(row):
    parts=[p.strip() for p in str(row.get('priority_type','')).split('+')]
    if 'CONSOLE' in parts:return 0
    if 'CO-LOAD' in parts:return 1
    if any('CANDIDATE' in p for p in parts):return 2
    return 3

class OperationsDashboard:
    def __init__(self,parent,controller,embedded=False):
        self.controller=controller;self.embedded=embedded
        self.window=tk.Frame(parent,bg='#FFFFFF') if embedded else tk.Toplevel(parent)
        if embedded:self.window.pack(fill='both',expand=True)
        else:
            self.window.title('ActivTrack Operations Dashboard');self.window.configure(bg='#FFFFFF');self.window.attributes('-fullscreen',True)
            self.window.bind('<Escape>',lambda e:self.window.attributes('-fullscreen',False))
            self.window.bind('<F11>',lambda e:self.window.attributes('-fullscreen',not self.window.attributes('-fullscreen')))
        self.closed=False;self.records={};self.vessels={};self.last_render=None;self.copy_until=0
        try:bootstrap_vessel_results()
        except (OSError,ValueError,KeyError):pass
        if not embedded:self.window.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style();style.configure('Ops.Treeview',background='#FFFFFF',foreground='#172033',rowheight=34,font=('Segoe UI',10),fieldbackground='#FFFFFF')
        style.configure('Ops.Treeview.Heading',font=('Segoe UI Semibold',9),background='#F5F5F7',foreground='#62626B',padding=(8,10))
        header=tk.Frame(self.window,bg='#111D32');header.pack(fill='x')
        tk.Label(header,text='ACTIVTRACK  /  OPERATIONS',font=('Segoe UI',15,'bold'),fg='white',bg='#111D32').pack(side='left',padx=18,pady=14)
        if not embedded:ttk.Button(header,text='Close',command=self.close).pack(side='right',padx=8)
        ttk.Button(header,text='Export FCL Excel',command=self.export).pack(side='right',padx=6)
        ttk.Button(header,text='Container History',command=self.history).pack(side='right',padx=6)
        ttk.Button(header,text='Stop checks',command=self.controller.stop.set).pack(side='right',padx=6)
        ttk.Button(header,text='Check Now',style='Blue.TButton',command=self.check).pack(side='right',padx=6)
        controls=tk.Frame(self.window,bg='#0B1220');controls.pack(fill='x',padx=16,pady=8)
        self.query=tk.StringVar();self.carrier=tk.StringVar(value='All carriers');self.status_filter=tk.StringVar(value='All statuses')
        tk.Label(controls,text='Search:',bg='#0B1220',fg='#B9C8DD',font=('Segoe UI',10,'bold')).pack(side='left',padx=(0,6))
        ttk.Entry(controls,textvariable=self.query,width=28).pack(side='left',padx=(0,8))
        self.carrier_box=ttk.Combobox(controls,textvariable=self.carrier,state='readonly',width=18,values=['All carriers']);self.carrier_box.pack(side='left',padx=6)
        self.status_box=ttk.Combobox(controls,textvariable=self.status_filter,state='readonly',width=18,values=['All statuses','LOADED','IN TRANSIT','ARRIVED','N/A','VESSEL CHANGED','VOYAGE CHANGED','NOT CHECKED','VERIFY REQUIRED','MISSING / INVALID','MANUAL CHECK','TRACKER ERROR','REVIEW DATA','CHECK FAILED','UNSUPPORTED','NO CARRIER RECORD']);self.status_box.pack(side='left',padx=6)
        self.timing=tk.StringVar();tk.Label(controls,textvariable=self.timing,bg='#0B1220',fg='#B9C8DD',font=('Segoe UI',10,'bold')).pack(side='right')
        for v in (self.query,self.carrier,self.status_filter):v.trace_add('write',lambda *a:setattr(self,'last_render',None))
        style.configure('Ops.TNotebook',background='#FFFFFF',borderwidth=0)
        style.configure('Ops.TNotebook.Tab',padding=(20,11),font=('Segoe UI Semibold',10),background='#F5F5F7',foreground='#62626B')
        style.map('Ops.TNotebook.Tab',background=[('selected','#111113')],foreground=[('selected','#FFFFFF')])
        self.views=ttk.Notebook(self.window,style='Ops.TNotebook');self.views.pack(fill='both',expand=True,padx=14,pady=(0,8))
        left=tk.Frame(self.views,bg='#111D32');right=tk.Frame(self.views,bg='#111D32')
        self.views.add(left,text='FCL Container Tracking');self.views.add(right,text='Vessel Changes')
        self.views.bind('<<NotebookTabChanged>>',self.change_view)
        self.fcl_heading=tk.StringVar(value='FCL CONTAINERS · MSC FIRST');tk.Label(left,textvariable=self.fcl_heading,bg='#111D32',fg='white',font=('Segoe UI',12,'bold')).pack(anchor='w',padx=12,pady=10)
        grid=tk.Frame(left,bg='#111D32');grid.pack(fill='both',expand=True)
        self.fixed=self.tree(grid,[('vs','VS ID',100),('vessel','VESSEL',270),('container','CONTAINER',150),('status','STATUS',145)])
        self.fixed.grid(row=0,column=0,sticky='nsew')
        self.details=self.tree(grid,[('recorded','FIRST RECORDED',135),('voyage','VOYAGE',110),('pol','POL',85),('pod','POD',85),('departure','POL DEPARTURE',165),('check','LAST CHECKED',205),('booking','BOOKING',120)])
        self.details.grid(row=0,column=1,sticky='nsew');grid.rowconfigure(0,weight=1);grid.columnconfigure(1,weight=1)
        sy=ttk.Scrollbar(grid,orient='vertical',command=lambda *a:self.scroll_both(*a));sy.grid(row=0,column=2,sticky='ns')
        self.fixed.configure(yscrollcommand=sy.set);self.details.configure(yscrollcommand=sy.set)
        sx=ttk.Scrollbar(grid,orient='horizontal',command=self.details.xview);sx.grid(row=1,column=1,sticky='ew');self.details.configure(xscrollcommand=sx.set)
        for tree in (self.fixed,self.details):
            tree.bind('<MouseWheel>',self.wheel);tree.bind('<Double-1>',self.show_record)
            tree.tag_configure('LOADED',background='#FEF3C7',foreground='#92400E')
            tree.tag_configure('ARRIVED',background='#DCFCE7',foreground='#14532D')
            tree.tag_configure('IN TRANSIT',background='#EFF6FF',foreground='#DC2626')
            tree.tag_configure('N/A',background='#F1F5F9',foreground='#64748B')
            tree.tag_configure('VESSEL CHANGED',background='#FEE2E2',foreground='#991B1B')
            tree.tag_configure('VOYAGE CHANGED',background='#FEE2E2',foreground='#991B1B')
        self.details.tag_configure('DEPARTED',background='#166534',foreground='#FFFFFF')
        self.fixed.bind('<Button-1>',lambda e:self.copy_cell(e,'fcl'))
        ttk.Button(left,text='View evidence / verify selected container',command=self.show_record).pack(anchor='e',padx=10,pady=8)
        self.vessel_heading=tk.StringVar(value='VESSEL DATE CHANGES · CONSOLES FIRST');tk.Label(right,textvariable=self.vessel_heading,bg='#111D32',fg='white',font=('Segoe UI',12,'bold')).pack(anchor='w',padx=12,pady=10)
        vf=tk.Frame(right);vf.pack(fill='both',expand=True)
        self.changes=self.tree(vf,[('vs','VS ID',100),('vessel','VESSEL',260),('type','PRIORITY',205),('etd','ETD: CURRENT → LATEST',230),('eta','ETA: CURRENT → LATEST',230),('voyage','VOYAGE',120),('pol','POL',85),('pod','POD',85),('reason','DETECTED CHANGE',360)])
        self.change_identity=self.tree(vf,[('vs','VS ID',100),('vessel','VESSEL',235)])
        self.change_identity.grid(row=0,column=0,sticky='nsew')
        self.changes.configure(displaycolumns=('type','etd','eta','voyage','pol','pod','reason'))
        self.changes.grid(row=0,column=1,sticky='nsew');vf.rowconfigure(0,weight=1);vf.columnconfigure(1,weight=1)
        y=ttk.Scrollbar(vf,orient='vertical',command=lambda *a:self.scroll_changes(*a));y.grid(row=0,column=2,sticky='ns');self.changes.configure(yscrollcommand=y.set);self.change_identity.configure(yscrollcommand=y.set)
        x=ttk.Scrollbar(vf,orient='horizontal',command=self.changes.xview);x.grid(row=1,column=1,sticky='ew');self.changes.configure(xscrollcommand=x.set)
        self.changes.tag_configure('DELAYED',foreground='#B91C1C');self.changes.tag_configure('ADVANCED',foreground='#15803D')
        self.changes.tag_configure('CONSOLE',background='#F1E7FA')
        self.changes.tag_configure('COLOAD',background='#E7F1FD')
        self.changes.bind('<Double-1>',self.show_vessel)
        self.changes.bind('<Button-1>',lambda e:self.copy_cell(e,'vessel'))
        self.changes.bind('<Button-3>', self.notice_menu)
        self.change_identity.bind('<Button-1>',lambda e:self.copy_cell(e,'vessel'))
        self.change_identity.bind('<Button-3>',self.notice_menu)
        self.change_identity.bind('<Double-1>',self.show_vessel)
        for tree in (self.change_identity,self.changes):
            tree.bind('<MouseWheel>',lambda e:(self.scroll_changes('scroll',-int(e.delta/120),'units'),'break')[1])
            tree.bind('<<TreeviewSelect>>',lambda e:self.sync_change_selection(e.widget))
            tree.tag_configure('CONSOLE',background='#F1E7FA');tree.tag_configure('COLOAD',background='#E7F1FD')
        self.source_text=tk.StringVar();tk.Label(right,textvariable=self.source_text,bg='#111D32',fg='#B9C8DD',font=('Segoe UI',9),wraplength=650,justify='left').pack(fill='x',padx=10,pady=8)
        self.message=tk.StringVar();tk.Label(self.window,textvariable=self.message,bg='#0B1220',fg='#D1DEEE',anchor='w',font=('Segoe UI',10)).pack(fill='x',padx=16,pady=(0,10))
        from desktop_ui import lighten_widgets
        lighten_widgets(self.window)
        try:self.controller.load_queue()
        except (OSError,ValueError):pass
        self.tick()
        self.views.select(1)
    def change_view(self,event=None):
        if not hasattr(self,'status_box'):return
        self.status_box.configure(state='readonly' if self.views.index('current')==0 else 'disabled')
    def notice_row(self, row):
        """Preserve the clicked snapshot and resolve first/second-leg context."""
        snapshot = dict(row)
        try:
            path = self.controller.source()
            if path:
                path = Path(path)
                key = (str(path), path.stat().st_mtime_ns)
                if getattr(self,'_notice_source_key',None) != key:
                    self._notice_source = json.loads(path.read_text(encoding='utf-8-sig'))
                    self._notice_source_key = key
                for schedule in self._notice_source.get('schedules',[]):
                    ref = str(schedule.get('reference_number') or f"VS-{schedule.get('id')}")
                    if ref == str(snapshot.get('vs_id')):
                        snapshot['total_legs'] = len(schedule.get('legs') or [schedule])
                        break
        except (OSError, ValueError, TypeError):
            pass
        return snapshot

    def notice_menu(self, event):
        tree=getattr(event,'widget',self.changes)
        iid = tree.identify_row(event.y)
        if not iid or tree.identify_region(event.x,event.y) != 'cell':
            return
        tree.selection_set(iid)
        row = self.notice_row(self.vessels.get(iid,{}))
        if not row:
            return
        from notice_tools import build_notice, NoticeTools
        try:
            payload = build_notice(row)
        except ValueError as exc:
            self.message.set(str(exc));self.copy_until=time.monotonic()+8
            return
        menu = tk.Menu(self.window,tearoff=False)
        menu.add_command(label='Copy header', command=lambda:self.copy_notice(payload['header'],'Header'))
        menu.add_command(label='Copy remarks', command=lambda:self.copy_notice(payload['remarks'],'Remarks'))
        menu.add_command(label='Copy header + remarks', command=lambda:self.copy_notice(payload['header']+'\n\n'+payload['remarks'],'Header and remarks'))
        menu.add_separator()
        menu.add_command(label='Preview / edit notice…',command=lambda:NoticeTools(self.window,row))
        try:
            menu.tk_popup(event.x_root,event.y_root)
        finally:
            menu.grab_release()
        return 'break'

    def copy_notice(self,text,label):
        from notice_tools import copy_text
        try:
            copy_text(self.window,text)
            self.copy_until=time.monotonic()+8
            self.message.set(label+' copied · Clipboard only; nothing sent')
        except tk.TclError as exc:
            messagebox.showerror('Clipboard unavailable',str(exc),parent=self.window)
    def copy_cell(self,event,view):
        tree=event.widget
        if view=='vessel' and tree is self.changes:return
        if tree.identify_region(event.x,event.y)!='cell':return
        column=tree.identify_column(event.x);iid=tree.identify_row(event.y)
        if column not in ('#1','#2') or not iid:return
        record=(self.records if view=='fcl' else self.vessels).get(iid)
        if not record:return
        value=str(record.get('vs_id' if column=='#1' else 'vessel') or '')
        if value:
            self.window.clipboard_clear();self.window.clipboard_append(value);self.window.update_idletasks()
            self.copy_until=time.monotonic()+5;self.message.set('Copied: '+value)
    def tree(self,parent,columns):
        tree=ttk.Treeview(parent,columns=[c[0] for c in columns],show='headings',style='Ops.Treeview',selectmode='browse')
        for name,label,width in columns:tree.heading(name,text=label);tree.column(name,width=width,minwidth=60,stretch=False)
        return tree
    def scroll_both(self,*args):self.fixed.yview(*args);self.details.yview(*args)
    def scroll_changes(self,*args):self.change_identity.yview(*args);self.changes.yview(*args)
    def sync_change_selection(self,tree):
        selected=tree.selection()
        other=self.changes if tree is self.change_identity else self.change_identity
        if selected and other.selection()!=selected:other.selection_set(selected)
    def wheel(self,event):self.scroll_both('scroll',-int(event.delta/120),'units');return 'break'
    def check(self):
        if not self.controller.start():self.message.set(self.controller.message if not self.controller.busy else 'A check is already running')
    def show_record(self,event=None):
        selected=(event.widget.selection() if event else self.fixed.selection() or self.details.selection())
        if not selected:return
        r=self.records.get(selected[0]);
        if not r:return
        window=tk.Toplevel(self.window);window.title(r['container'] or r['booking_id']);window.geometry('760x560')
        text=tk.Text(window,wrap='word',font=('Segoe UI',10));text.pack(fill='both',expand=True,padx=12,pady=12)
        summary={k:r.get(k) for k in ('booking_id','vs_id','container','vessel','voyage','pol','pod','operator','owner','status','departure','reason','checked_at','snapshot_arrived','recorded_date','recording_basis','first_seen_at','previous_seen_at','previous_containers','booking_updated_date','checks','evidence')}
        text.insert('1.0',json.dumps(summary,indent=2,ensure_ascii=False));text.configure(state='disabled')
        ttk.Label(window,text='Verification opens visible Edge for 60 seconds. Status changes only from supported actual events.').pack(padx=12)
        providers=list(dict.fromkeys(p for p in (r['operator'],r['owner']) if p in SITES))
        for provider in providers:
            ttk.Button(window,text='Verify '+provider,command=lambda p=provider:self.controller.start(verification=(r['key'],p))).pack(side='left',padx=12,pady=12)
    def history(self):
        from container_history import current_entries
        rows=current_entries(json.loads(self.controller.source().read_text(encoding='utf-8-sig')))
        win=tk.Toplevel(self.window);win.title('Container Recording History');win.geometry('1180x600')
        query=tk.StringVar();ttk.Label(win,text='Search container, booking, HBL or reference:').pack(anchor='w',padx=12,pady=(12,0))
        ttk.Entry(win,textvariable=query).pack(fill='x',padx=12,pady=8)
        frame=ttk.Frame(win);frame.pack(fill='both',expand=True,padx=12)
        cols=[('booking_id','BOOKING',100),('reference','REFERENCE',110),('hbl','HBL',145),('container','CONTAINER',145),('recorded_date','FIRST RECORDED',130),('recording_basis','DATE BASIS',380),('first_seen_at','FIRST OBSERVED',230),('previous_seen_at','PREVIOUS SNAPSHOT',230)]
        tree=self.tree(frame,cols);tree.grid(row=0,column=0,sticky='nsew');frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        sy=ttk.Scrollbar(frame,command=tree.yview);sy.grid(row=0,column=1,sticky='ns');tree.configure(yscrollcommand=sy.set)
        sx=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview);sx.grid(row=1,column=0,sticky='ew');tree.configure(xscrollcommand=sx.set)
        def render(*args):
            tree.delete(*tree.get_children());q=query.get().upper()
            for r in rows:
                if not q or q in ' '.join(str(v) for v in r.values()).upper():tree.insert('','end',values=[r.get(k,'') for k,label,width in cols])
        query.trace_add('write',render);render()
        ttk.Label(win,text='First observed is the earliest available snapshot. Booking updated date alone does not prove when a container was entered.').pack(padx=12,pady=12)
    def show_vessel(self,event=None):
        tree=event.widget if event else self.changes
        if not tree.selection():return
        row=self.vessels[tree.selection()[0]]
        messagebox.showinfo('Schedule change evidence',json.dumps(row,indent=2,ensure_ascii=False),parent=self.window)
    def export(self):
        from openpyxl import Workbook
        wb=Workbook();wb.remove(wb.active);add_fcl_sheet(wb,self.controller.snapshot().get('containers',[]))
        from container_history import add_history_sheet
        add_history_sheet(wb,json.loads(self.controller.source().read_text(encoding='utf-8-sig')))
        folder=REPORTS/'FCL Tracking';folder.mkdir(parents=True,exist_ok=True);p=folder/f'FCL_Cargo_{datetime.now(COLOMBO):%Y%m%d_%H%M%S}.xlsx';wb.save(p);os.startfile(p)
    def tick(self):
        if self.closed:return
        state=self.controller.snapshot();vstate={}
        try:vstate=json.loads(VESSEL_STATE.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        signature=(str(state),str(vstate),self.query.get(),self.carrier.get(),self.status_filter.get())
        if signature!=self.last_render:
            self.last_render=signature;self.render(state,vstate)
        if time.monotonic()>=self.copy_until:self.message.set(self.controller.message+'  |  Click a VS ID or vessel name to copy · Right-click a vessel change for notice text')
        next_at=next_check(datetime.now(COLOMBO));last=state.get('last_round','Not checked')
        self.timing.set(f'Last: {last[11:16] if len(last)>16 else last}  |  Next: {next_at:%d %b %H:%M} SL')
        self.window.after(1500,self.tick)
    def render(self,state,vstate):
        query=self.query.get().upper();carrier=self.carrier.get();status=self.status_filter.get()
        rows=state.get('containers',[]);owners=sorted({r['operator'] for r in rows if r.get('operator')});self.carrier_box.configure(values=['All carriers']+owners)
        y=self.fixed.yview();selected=self.fixed.selection() or self.details.selection()
        for tree in (self.fixed,self.details,self.changes,self.change_identity):tree.delete(*tree.get_children())
        self.records={};self.vessels={}
        for r in sorted(rows,key=lambda r:(r.get('priority',1),r.get('eta') or '9999',r.get('container',''))):
            if carrier!='All carriers' and r.get('operator')!=carrier:continue
            if status!='All statuses' and r.get('status','N/A')!=status:continue
            if query and query not in ' '.join(str(r.get(k,'')) for k in ('container','booking_id','vs_id','vessel','customer')).upper():continue
            iid=r['key'];self.records[iid]=r;tag=r.get('status') or 'N/A'
            self.fixed.insert('','end',iid=iid,values=(r['vs_id'],r['vessel'],r['container'] or 'MISSING',tag),tags=(tag,))
            self.details.insert('','end',iid=iid,values=(short_date(r.get('recorded_date')),r['voyage'],short_port(r['pol'],r.get('pol_code')),short_port(r['pod'],r.get('pod_code')),r.get('departure','NOT CONFIRMED'),r.get('checked_at',''),r['booking_id']),tags=('DEPARTED' if r.get('departure')=='DEPARTED' and tag!='ARRIVED' else tag,))
        if y:self.scroll_both('moveto',y[0])
        if selected and selected[0] in self.records:
            self.fixed.selection_set(selected[0]);self.details.selection_set(selected[0])
        changed=[r for r in vstate.get('rows',[]) if any(s in r.get('result','') for s in ('ETD DELAYED','ETD ADVANCED','ETA DELAYED','ETA ADVANCED'))]
        changed.sort(key=lambda r:(console_rank(r),r.get('carrier_eta') or r.get('at_eta') or '9999',r.get('vs_id','')))
        for r in changed:
            if query and query not in (' '.join(str(r.get(k,'')) for k in ('vessel','vs_id','container_numbers','booking_refs'))).upper():continue
            if carrier!='All carriers' and carrier.lower() not in str(r.get('tracking_site') or r.get('carrier','')).lower():continue
            iid=str(len(self.vessels));self.vessels[iid]=r
            priority=str(r.get('priority_type') or '')
            tags=('CONSOLE' if 'CONSOLE' in priority else 'COLOAD' if 'CO-LOAD' in priority else '', 'DELAYED' if 'DELAYED' in r.get('result','') else 'ADVANCED')
            self.changes.insert('','end',iid=iid,values=(r.get('vs_id'),r.get('vessel'),r.get('priority_type') or 'OTHER',f"{short_date(r.get('at_etd'))} → {short_date(r.get('carrier_etd'))}",f"{short_date(r.get('at_eta'))} → {short_date(r.get('carrier_eta'))}",r.get('voyage'),short_port(r.get('pol')),short_port(r.get('pod')),r.get('result')),tags=tags)
            self.change_identity.insert('','end',iid=iid,values=(r.get('vs_id'),r.get('vessel')),tags=(tags[0],))
        self.fcl_heading.set(f'FCL CONTAINERS · MSC FIRST  |  {len(self.records)} active records  |  {state.get('excluded_past_arrivals',0)} past arrivals excluded')
        self.vessel_heading.set(f'VESSEL DATE CHANGES · CONSOLES FIRST  |  {len(self.vessels)}')
        self.source_text.set('FCL capture: '+str(state.get('source','No capture yet'))+'\nVessel run: '+str(vstate.get('checked_at','Run Automated Tracking to populate changes')))
    def close(self):self.closed=True;self.window.destroy()
