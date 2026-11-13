"""Approved guided desktop workspace; delegates operations to the existing app."""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk

WHITE='#FFFFFF'; BLACK='#111113'; TEXT='#161619'; MUTED='#62626B'
BORDER='#E2E2E7'; RED='#E52329'; PURPLE='#7934CE'; BLUE='#2464DF'
FOOTER='© Globactive Logistics Sri lanka | developed by Clement R.'

def load_header_logo(path,master):
    """Render the transparent master at screen resolution with smooth edges."""
    try:
        from PIL import Image, ImageTk
        with Image.open(path) as source:
            image=source.convert('RGBA')
            bounds=image.getchannel('A').getbbox()
            if bounds:image=image.crop(bounds)
            scale=max(1.0,float(master.winfo_fpixels('1i'))/96.0)
            width=round(245*scale);height=round(52*scale)
            image.thumbnail((width,height),Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image,master=master)
    except ImportError:
        raw=tk.PhotoImage(file=str(path),master=master)
        return raw.subsample(max(1,round(raw.width()/245)))

def lighten_widgets(widget):
    """Keep existing tool logic while giving legacy Tk surfaces the shared palette."""
    kind=widget.winfo_class()
    try:
        if kind in ('Frame','Toplevel','Labelframe'):widget.configure(bg=WHITE)
        elif kind=='Text':widget.configure(bg='#FAFAFC',fg=TEXT,insertbackground=RED,highlightbackground=BORDER)
        elif kind=='Label':widget.configure(bg=WHITE,fg=TEXT)
    except tk.TclError:pass
    for child in widget.winfo_children():lighten_widgets(child)

def configure_style():
    style=ttk.Style()
    try:style.theme_use('clam')
    except tk.TclError:pass
    for name in ('TFrame','Card.TFrame'):
        style.configure(name,background=WHITE)
    for name in ('TLabel','Card.TLabel'):
        style.configure(name,background=WHITE,foreground=TEXT,font=('Segoe UI',10))
    for name in ('Hint.TLabel','Footer.TLabel','Eyebrow.TLabel'):
        style.configure(name,background=WHITE,foreground=MUTED,font=('Segoe UI',9))
    style.configure('Step.TLabel',background=WHITE,foreground=TEXT,font=('Segoe UI Semibold',12))
    style.configure('Display.TLabel',background=WHITE,foreground=TEXT,font=('Segoe UI Semibold',19))
    for name,color,foreground in [('Primary',RED,WHITE),('Secondary',BLACK,WHITE),('Purple',PURPLE,WHITE),('Blue',BLUE,WHITE),('Danger','#B71C29',WHITE),('Quiet','#F1F1F4',TEXT)]:
        style.configure(name+'.TButton',font=('Segoe UI Semibold',10),padding=(16,10),background=color,foreground=foreground,borderwidth=0)
        style.map(name+'.TButton',background=[('disabled','#E9E9ED'),('active',color)],foreground=[('disabled','#8A8A93')])
    style.configure('TEntry',padding=7,fieldbackground=WHITE,foreground=TEXT,insertcolor=RED,bordercolor='#CCCCD3')
    style.configure('TCombobox',padding=6,fieldbackground=WHITE,foreground=TEXT)
    style.map('TCombobox',fieldbackground=[('readonly',WHITE)],foreground=[('readonly',TEXT)])
    style.configure('TButton',padding=(10,7),font=('Segoe UI',9),background='#F1F1F4',foreground=TEXT)
    style.configure('TCheckbutton',background=WHITE,foreground=TEXT,font=('Segoe UI',9))
    style.configure('Card.TCheckbutton',background=WHITE,foreground=TEXT,font=('Segoe UI',9))
    style.map('Card.TCheckbutton',background=[('active',WHITE)],foreground=[('disabled','#888891')])
    style.configure('Horizontal.TProgressbar',background=RED,troughcolor='#EEEEF1',borderwidth=0,lightcolor=RED,darkcolor=RED)
    style.configure('App.Treeview',background=WHITE,fieldbackground=WHITE,foreground=TEXT,rowheight=36,font=('Segoe UI',9),borderwidth=0)
    style.configure('App.Treeview.Heading',background='#F5F5F7',foreground=MUTED,font=('Segoe UI Semibold',9),padding=(8,10),borderwidth=0)
    style.map('App.Treeview',background=[('selected','#EAE5F5')],foreground=[('selected',TEXT)])
    style.configure('TNotebook',background=WHITE,borderwidth=0)
    style.configure('TNotebook.Tab',padding=(14,9),background='#F5F5F7',foreground=MUTED,font=('Segoe UI',9))
    style.map('TNotebook.Tab',background=[('selected',BLACK)],foreground=[('selected',WHITE)])

class Panel(tk.Frame):
    def __init__(self,parent,fill=WHITE,**kwargs):
        super().__init__(parent,bg=WHITE,**kwargs)
        self.surface=tk.Canvas(self,highlightthickness=0,bg=WHITE,bd=0)
        self.surface.place(x=0,y=0,relwidth=1,relheight=1)
        self.body=tk.Frame(self,bg=fill)
        self.body.pack(fill='both',expand=True,padx=16,pady=14)
        def draw(e):
            w,h=e.width-1,e.height-1;r=13
            self.surface.delete('all')
            self.surface.create_polygon(r,1,w-r,1,w,1,w,r,w,h-r,w,h,w-r,h,r,h,1,h,1,h-r,1,r,1,1,smooth=True,fill=fill,outline=BORDER)
        self.bind('<Configure>',draw)

class ScrollPage(tk.Frame):
    def __init__(self,parent):
        super().__init__(parent,bg=WHITE)
        canvas=tk.Canvas(self,bg=WHITE,highlightthickness=0)
        bar=ttk.Scrollbar(self,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left',fill='both',expand=True);bar.pack(side='right',fill='y')
        self.body=tk.Frame(canvas,bg=WHITE)
        item=canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        # Scroll only while the pointer is over this page, not other application windows.
        def wheel(e):
            if canvas.winfo_height()<self.body.winfo_reqheight():canvas.yview_scroll(-int(e.delta/120),'units')
        self.body.bind('<MouseWheel>',wheel)
        canvas.bind('<MouseWheel>',wheel)
        self.canvas=canvas

def label(parent,text,size=10,bold=False,color=TEXT,**kwargs):
    return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=color,font=('Segoe UI Semibold' if bold else 'Segoe UI',size),justify='left',anchor='w',**kwargs)

class DesktopUI:
    def __init__(self,app):
        self.app=app;self.root=app.root;self.active_page='overview';self.active_mode='auto'
        self.report_source=None;self.report_cache_key=None;self.report_loading=False
        self.nav={};self.pages={};self.runners={}
        self.root.configure(bg=WHITE)
        self.root.geometry('1380x920');self.root.minsize(1120,760)
        shell=tk.Frame(self.root,bg=WHITE);shell.pack(fill='both',expand=True)
        shell.rowconfigure(0,weight=1);shell.columnconfigure(1,weight=1)
        side=tk.Frame(shell,bg=BLACK,width=196);side.grid(row=0,column=0,sticky='nsew');side.pack_propagate(False)
        label(side,'Vessel Report Generator',11,True,color=WHITE,wraplength=172).pack(anchor='w',padx=17,pady=(46,3))
        label(side,'Sea Import Operations',9,color='#C8C8CE').pack(anchor='w',padx=17,pady=(0,23))
        for key,text in [('overview','⌂   Overview'),('reports','▤   Reports'),('tracking','♧   Tracking'),('dashboard','▥   Live Dashboard'),('notices','▧   Notice Headers\n     & Remarks'),('alerts','♧   Alerts')]:
            button=tk.Button(side,text=text,bg=BLACK,fg=WHITE,activebackground='#323237',activeforeground=WHITE,font=('Segoe UI',10),anchor='w',justify='left',relief='flat',bd=0,padx=14,pady=12,command=lambda k=key:self.show(k))
            button.pack(fill='x',padx=10,pady=3);self.nav[key]=button
        tk.Button(side,text='▱  Open Reports Folder',bg=BLACK,fg='#DDDDDF',font=('Segoe UI',9),relief='flat',anchor='w',command=app._open_output_folder).pack(side='bottom',fill='x',padx=13,pady=19)
        main=tk.Frame(shell,bg=WHITE);main.grid(row=0,column=1,sticky='nsew',padx=(23,22));main.columnconfigure(0,weight=1);main.rowconfigure(1,weight=1)
        header=tk.Frame(main,bg=WHITE,height=82);header.grid(row=0,column=0,sticky='ew');header.pack_propagate(False)
        self.title=tk.StringVar(value='Daily Operations')
        tk.Label(header,textvariable=self.title,bg=WHITE,fg=TEXT,font=('Segoe UI Semibold',16)).pack(side='left')
        label(header,'Guided workspace',9,color=MUTED).pack(side='right')
        path=Path(__file__).parent/'assets'/'activtrack-logo.png'
        if path.is_file():
            self.logo=load_header_logo(path,header)
            tk.Label(header,image=self.logo,bg=WHITE).place(relx=.5,rely=.5,anchor='center')
        else:label(header,'ActivTrack',24,True).place(relx=.5,rely=.5,anchor='center')
        self.host=tk.Frame(main,bg=WHITE);self.host.grid(row=1,column=0,sticky='nsew');self.host.rowconfigure(0,weight=1);self.host.columnconfigure(0,weight=1)
        for name in ('overview','reports','tracking'):
            page=ScrollPage(self.host);page.grid(row=0,column=0,sticky='nsew');self.pages[name]=page
        self.pages['dashboard']=tk.Frame(self.host,bg=WHITE);self.pages['dashboard'].grid(row=0,column=0,sticky='nsew')
        self.build_overview();self.build_reports();self.build_tracking()
        activity=Panel(main);activity.grid(row=2,column=0,sticky='ew',pady=(10,9));activity.body.columnconfigure(1,weight=1)
        label(activity.body,'☷   System Activity',11,True).grid(row=0,column=0,sticky='w')
        ttk.Button(activity.body,text='Import Saved JSON',style='Quiet.TButton',command=app._import_json).grid(row=0,column=2,padx=(10,0))
        app.progress=ttk.Progressbar(activity.body,mode='determinate',value=0,length=150);app.progress.grid(row=0,column=1,sticky='ew',padx=20)
        self.activity_status=tk.Label(activity.body,textvariable=app.status_var,bg=WHITE,fg=MUTED,font=('Segoe UI',9),anchor='w')
        self.activity_status.grid(row=1,column=0,columnspan=3,sticky='ew',pady=(6,4))
        app.log_box=tk.Text(activity.body,height=3,wrap='word',bg=WHITE,fg=MUTED,relief='flat',highlightthickness=0,font=('Segoe UI',9),state='disabled')
        app.log_box.grid(row=2,column=0,columnspan=3,sticky='ew')
        tk.Label(self.root,text=FOOTER,bg=WHITE,fg=MUTED,font=('Segoe UI',9)).pack(side='bottom',before=shell,fill='x',pady=(1,9))
        app._log('Ready. Open ActiveTrack, set your booking range in Edge, then capture all pages.')
        self.show('overview');self.refresh_state()
        if self.root.state()!='withdrawn':self.root.after_idle(lambda:self.root.state('zoomed'))

    def card(self,parent,title,subtitle='',**kwargs):
        panel=Panel(parent,**kwargs)
        if title:label(panel.body,title,14,True).pack(anchor='w')
        if subtitle:label(panel.body,subtitle,9,color=MUTED,wraplength=640).pack(anchor='w',pady=(3,10))
        return panel

    def settings(self,parent):
        a=self.app;box=self.card(parent,'⚙  Report settings');b=box.body
        row=tk.Frame(b,bg=WHITE);row.pack(fill='x',pady=(9,5));label(row,'As-of date',9).pack(side='left')
        ttk.Entry(row,textvariable=a.as_of_var,width=14).pack(side='right')
        row=tk.Frame(b,bg=WHITE);row.pack(fill='x',pady=5);label(row,'Output folder',9).pack(side='left')
        ttk.Button(row,text='Browse',style='Quiet.TButton',command=a._browse_output).pack(side='right')
        ttk.Entry(b,textvariable=a.output_var).pack(fill='x',pady=(0,7))
        ttk.Checkbutton(b,text='Save source snapshot',variable=a.save_json_var,style='Card.TCheckbutton').pack(anchor='w')
        return box

    def build_overview(self):
        a=self.app;p=self.pages['overview'].body
        workflow=Panel(p);workflow.pack(fill='x',pady=(0,13));b=workflow.body
        label(b,"Today's workflow",13,True).grid(row=0,column=0,sticky='nw',padx=(0,22))
        steps=[('1','Open & sign in','Open ActiveTrack in Edge\nand sign in.'),('2','Set date range','Choose bookings on the\nsame Edge page.'),('3','Capture & save','Capture all filtered pages.\nSave Excel + source.'),('4','Track & review','Check one carrier at a time\nusing the first capture.')]
        for i,(n,t,d) in enumerate(steps,1):
            frame=tk.Frame(b,bg=WHITE);frame.grid(row=0,column=i,sticky='nw',padx=(4,16));b.columnconfigure(i,weight=1)
            label(frame,n+'   '+t,10,True).pack(anchor='w');label(frame,d,9,color=MUTED).pack(anchor='w',pady=(6,0))
        middle=tk.Frame(p,bg=WHITE);middle.pack(fill='x');middle.columnconfigure(0,weight=3);middle.columnconfigure(1,weight=2)
        capture=self.card(middle,"▤  Start today's report",'Sign in, choose your bookings, then capture every filtered page.')
        capture.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        buttons=tk.Frame(capture.body,bg=WHITE);buttons.pack(fill='x',pady=(14,14))
        a.open_button=ttk.Button(buttons,text='↗  Open ActiveTrack',style='Secondary.TButton',command=a._open_activtrack);a.open_button.pack(side='left',padx=(0,15))
        a.generate_button=ttk.Button(buttons,text='▤  Capture & Generate Excel',style='Primary.TButton',command=a._generate_live);a.generate_button.pack(side='left')
        label(capture.body,'Use the same Edge page and selected booking range.',9,color=MUTED).pack(anchor='w')
        self.settings(middle).grid(row=0,column=1,sticky='nsew')
        label(p,'▦  Tools',15,True).pack(anchor='w',pady=(15,9))
        tools=tk.Frame(p,bg=WHITE);tools.pack(fill='x');tools.columnconfigure((0,1,2),weight=1,uniform='tool')
        specs=[('all_vessels_button','All Vessels','Export vessels from the selected bookings.',a._generate_all_vessels,BLACK,WHITE),('automated_tracking_button','Automated Tracking','Track carriers and compare vessel dates.',a._open_automated_tracking,PURPLE,'#F8F2FC'),('tracking_button','Manual Carrier Tracking','Choose a carrier and paste vessel names.',a._open_tracking,BLACK,WHITE),('long_term_button','Long Term Auto','Paste future sailings, track and compare.',a._open_long_term,BLUE,'#F0F6FF'),('dashboard_button','Live Dashboard','FCL status, date changes and notice text.',a._open_dashboard,BLACK,WHITE),('alerts_button','Arrival / Departure Alerts','Check vessel arrival and departure alerts.',lambda:a.arrival_alerts.open(),BLACK,WHITE)]
        for i,(attr,title,hint,command,color,fill) in enumerate(specs):
            panel=Panel(tools,fill=fill);panel.grid(row=i//3,column=i%3,sticky='nsew',padx=(0,9 if i%3<2 else 0),pady=(0,10))
            button=tk.Button(panel.body,text=title+'  ›',bg=fill,fg=color,activebackground=fill,activeforeground=color,font=('Segoe UI Semibold',12),bd=0,relief='flat',anchor='w',command=command)
            button.pack(fill='x');setattr(a,attr,button)
            label(panel.body,hint,9,color=MUTED,wraplength=260).pack(anchor='w',pady=(3,0))

    def build_reports(self):
        a=self.app;p=self.pages['reports'].body
        label(p,'Your booking reports',18,True).pack(anchor='w');label(p,'Capture every filtered page; review saved schedules and booking detail below.',9,color=MUTED).pack(anchor='w',pady=(3,12))
        actions=Panel(p);actions.pack(fill='x',pady=(0,12));b=actions.body
        for text,style,command in [('Open ActiveTrack','Secondary.TButton',a._open_activtrack),('Capture & Generate Excel','Primary.TButton',a._generate_live),('All Vessels','Quiet.TButton',a._generate_all_vessels)]:
            ttk.Button(b,text=text,style=style,command=command).pack(side='left',padx=(0,10))
        a.folder_button=ttk.Button(b,text='Open Reports Folder',style='Quiet.TButton',command=a._open_output_folder);a.folder_button.pack(side='right')
        a.import_button=ttk.Button(b,text='Import Saved JSON',style='Quiet.TButton',command=a._import_json);a.import_button.pack(side='right',padx=8)
        settings=tk.Frame(p,bg=WHITE);settings.pack(fill='x',pady=(0,13));label(settings,'Report date',9).pack(side='left');ttk.Entry(settings,textvariable=a.as_of_var,width=13).pack(side='left',padx=10);ttk.Checkbutton(settings,text='Save source snapshot',variable=a.save_json_var).pack(side='left',padx=12)
        self.report_status=tk.StringVar(value='Generate or import a report to see its exact captured records.')
        tk.Label(p,textvariable=self.report_status,bg=WHITE,fg=MUTED,font=('Segoe UI',9),anchor='w',wraplength=1000).pack(fill='x',pady=(0,8))
        self.report_tabs=ttk.Notebook(p);self.report_tabs.pack(fill='both',expand=True)
        self.report_trees={}
        for key,title in [('summary','Vessel Summary'),('console','Console & Co-load'),('details','Booking Details'),('atd','ATD Missing'),('ata','ATA Missing'),('arrivals','48HR Arrivals')]:
            page=tk.Frame(self.report_tabs,bg=WHITE);self.report_tabs.add(page,text=title)
            tree=ttk.Treeview(page,show='headings',style='App.Treeview',height=9)
            sy=ttk.Scrollbar(page,orient='vertical',command=tree.yview);sx=ttk.Scrollbar(page,orient='horizontal',command=tree.xview)
            tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set);tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew');page.columnconfigure(0,weight=1);page.rowconfigure(0,weight=1)
            tree.tag_configure('console',background='#F1E7FA');tree.tag_configure('coload',background='#E7F1FD');tree.tag_configure('fcl',font=('Segoe UI Semibold',9))
            self.report_trees[key]=tree
        label(p,'Purple = console · Light blue = co-load · Bold = FCL. Excel keeps container colours in the VS ID column only.',9,color=MUTED).pack(anchor='w',pady=9)

    def build_tracking(self):
        a=self.app;p=self.pages['tracking'].body
        label(p,'Track, compare, review',18,True).pack(anchor='w');label(p,'One carrier at a time. Compare current booking dates against the latest carrier dates.',9,color=MUTED).pack(anchor='w',pady=(3,12))
        self.source_text=tk.StringVar(value='Locked source: capture or import the booking report first.')
        tk.Label(p,textvariable=self.source_text,bg='#F5F5F7',fg=MUTED,font=('Segoe UI',9),anchor='w',padx=12,pady=11,wraplength=1050).pack(fill='x',pady=(0,12))
        modes=tk.Frame(p,bg=WHITE);modes.pack(fill='x');modes.columnconfigure((0,1,2),weight=1,uniform='tracking')
        self.mode_buttons={}
        for i,(key,text,hint,color) in enumerate([('auto','Automated Tracking','Group vessels and route to carrier bots.',PURPLE),('manual','Manual Carrier Tracking','Choose carrier and enter vessel names.',BLACK),('longterm','Long Term Auto','Paste future sailings to track and compare.',BLUE)]):
            box=Panel(modes);box.grid(row=0,column=i,sticky='ew',padx=(0,10 if i<2 else 0))
            button=tk.Button(box.body,text=text,bg=WHITE,fg=color,font=('Segoe UI Semibold',11),bd=0,anchor='w',command=lambda k=key:self.tracking_mode(k));button.pack(fill='x');self.mode_buttons[key]=button
            label(box.body,hint,9,color=MUTED,wraplength=265).pack(anchor='w',pady=(3,0))
        self.runner_host=tk.Frame(p,bg=WHITE);self.runner_host.pack(fill='both',expand=True,pady=(14,0))
        self.auto_intro=self.card(self.runner_host,'Daily vessel check','Uses this session’s first capture. Skips completed arrivals and checks carrier groups in sequence.')
        self.auto_intro.pack(fill='x')
        ttk.Button(self.auto_intro.body,text='▶  Start Automated Tracking',style='Purple.TButton',command=a._open_automated_tracking).pack(anchor='w',pady=(8,5))
        label(self.auto_intro.body,'Results: Action Required · Priority Watch · Manual Tracking · All Results',9,color=MUTED).pack(anchor='w',pady=8)

    def tracking_mode(self,mode):
        self.show('tracking');self.active_mode=mode
        for key,button in self.mode_buttons.items():button.configure(bg='#F2E8FC' if key==mode and key=='auto' else '#EAF1FD' if key==mode and key=='longterm' else '#F3F3F5' if key==mode else WHITE)
        self.auto_intro.pack_forget()
        for runner in self.runners.values():
            if runner.window.winfo_exists():runner.window.pack_forget()
        if mode=='auto':
            runner=self.runners.get('auto')
            if runner and runner.window.winfo_exists():runner.window.pack(fill='both',expand=True)
            else:self.auto_intro.pack(fill='x')
        elif mode=='manual':
            from tracking_window import TrackingWindow
            runner=self.runners.get(mode)
            if runner is None or not runner.window.winfo_exists():
                runner=TrackingWindow(self.runner_host,lambda:self.app.output_var.get(),embedded=True);self.runners[mode]=runner;self.app.tracking_window=runner
                runner.can_start=lambda:not self.other_tracking_busy('manual')
            runner.window.pack(fill='both',expand=True)
        elif mode=='longterm':
            from long_term_tracking import LongTermWindow
            runner=self.runners.get(mode)
            if runner is None or not runner.window.winfo_exists():
                runner=LongTermWindow(self.runner_host,self.app.output_var.get,embedded=True,source_getter=lambda:self.app.morning_source_path);self.runners[mode]=runner;self.app.long_term_window=runner
                runner.can_start=lambda:not self.other_tracking_busy('longterm')
            runner.window.pack(fill='both',expand=True)

    def other_tracking_busy(self,mode):
        return self.app.busy or self.app.fcl_controller.busy or self.app.fcl_controller.queued or any(getattr(runner,'busy',False) for key,runner in self.runners.items() if key!=mode)

    def show(self,name):
        if name=='notices':self.app._open_notice_tools();return
        if name=='alerts':self.app.arrival_alerts.open();return
        if name not in self.pages:return
        self.active_page=name;self.pages[name].tkraise()
        self.title.set({'overview':'Daily Operations','reports':'Reports','tracking':'Tracking','dashboard':'Live Dashboard'}[name])
        for key,button in self.nav.items():button.configure(bg=RED if key==name else BLACK)
        if name=='reports':self.load_report()
        if name=='dashboard':
            if self.app.dashboard is None or self.app.dashboard.closed:
                from operations_dashboard import OperationsDashboard
                self.app.dashboard=OperationsDashboard(self.pages['dashboard'],self.app.fcl_controller,embedded=True)
        self.refresh_state()

    def refresh_state(self):
        source=self.app.morning_source_path
        self.source_text.set('Locked source: '+source.name if source else 'Locked source: capture or import the booking report first. Automated Tracking will use that first snapshot.')
        if self.active_page=='reports':self.load_report()

    def load_report(self):
        path=self.app.morning_source_path
        if not path:
            files=list(Path(self.app.output_var.get()).glob('ActivTrack_Vessel_Booking_Report_*_Source.json'))
            path=max(files,key=lambda p:p.stat().st_mtime) if files else None
        if not path or not path.is_file():return
        key=(str(path),path.stat().st_mtime_ns)
        if key==self.report_cache_key or self.report_loading:return
        self.report_loading=True;self.report_status.set('Loading saved capture: '+path.name)
        future=self.app.executor.submit(lambda:json.loads(path.read_text(encoding='utf-8-sig')))
        def poll():
            if not future.done():self.root.after(100,poll);return
            self.report_loading=False
            try:
                source=future.result();self.render_report(source);self.report_source=source;self.report_cache_key=key
                kind='Session source' if self.app.morning_source_path else 'Previously saved capture (not a new capture)'
                self.report_status.set(kind+': '+path.name)
            except (OSError,ValueError,KeyError,TypeError) as exc:self.report_status.set('Saved capture needs attention: '+str(exc))
        self.root.after(80,poll)

    def render_report(self,source):
        from report_builder import normalize_source,_build_records
        from console_rules import booking_class,schedule_classes,agent_name
        from operations_dashboard import short_port,short_date
        data=normalize_source(source);details,legs,_=_build_records(data)
        raw={str(b.get('reference_number')):b for b in source.get('bookings',[])}
        classes=schedule_classes(source.get('bookings',[]))
        try:as_of=date.fromisoformat(self.app.as_of_var.get())
        except ValueError:as_of=date.today()
        for key,tree in self.report_trees.items():
            tree.delete(*tree.get_children())
            if key in ('details','console'):
                cols=[('VS ID',100),('Vessel',200),('Voyage',95),('Booking',105),('Type',95),('Agent',230),('Consignee',210),('HBL',155),('MBL',160),('Container',155),('POL',75),('POD',75)]
            else:cols=[('VS ID',100),('Vessel',205),('Voyage',100),('Leg',45),('POL',75),('POD',75),('ETD',105),('ATD',105),('ETA',105),('ATA',105),('Bookings',70),('Type',100),('Container',170)]
            tree.configure(columns=[str(i) for i in range(len(cols))])
            for i,(text,width) in enumerate(cols):tree.heading(str(i),text=text);tree.column(str(i),width=width,minwidth=45,stretch=False)
            if key in ('details','console'):
                records=details
                if key=='console':records=sorted([r for r in details if booking_class(raw.get(r['booking_ref'],{}))],key=lambda r:(booking_class(raw.get(r['booking_ref'],{}))!='CONSOLE',r['schedule_ref'],r['booking_ref']))
                for r in records:
                    b=raw.get(r['booking_ref'],{});kind=booking_class(b)
                    values=(r['schedule_ref'],r['vessel_name'],r['voyage'],r['booking_ref'],kind or b.get('shipment_type'),agent_name(b),r['customer'],b.get('hbl_number',''),b.get('mbl_number',''),b.get('container_number',''),short_port(r['pol']),short_port(r['pod']))
                    tags=('console',) if kind=='CONSOLE' else ('coload',) if kind=='CO-LOAD' else ('fcl',) if str(b.get('shipment_type')).upper()=='FCL' else ()
                    tree.insert('','end',values=values,tags=tags)
            else:
                for r in legs:
                    if key=='atd' and not(r['etd'] and r['etd']<=as_of and not r['atd']):continue
                    if key=='ata' and not(r['eta'] and r['eta']<=as_of and not r['ata']):continue
                    if key=='arrivals' and r['eta']!=as_of+timedelta(days=2):continue
                    kinds=classes.get(r['schedule_ref'],set())
                    if not kinds:
                        schedule=next((s for s in source.get('schedules',[]) if s.get('reference_number')==r['schedule_ref']),{})
                        kinds=classes.get(str(schedule.get('id')),set())
                    kind='CONSOLE' if 'CONSOLE' in kinds else 'CO-LOAD' if 'CO-LOAD' in kinds else ''
                    tags=list(('console',) if kind=='CONSOLE' else ('coload',) if kind=='CO-LOAD' else ())
                    if r.get('has_fcl_booking'):tags.append('fcl')
                    tree.insert('','end',values=(r['schedule_ref'],r['vessel_name'],r['voyage'],r['leg_no'],short_port(r['pol']),short_port(r['pod']),short_date(r['etd']),short_date(r['atd']),short_date(r['eta']),short_date(r['ata']),r['booking_count'],kind,', '.join(r.get('container_numbers') or [])),tags=tags)
