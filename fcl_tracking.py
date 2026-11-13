"""Container-first capture, evidence rules and scheduled tracking state."""
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

COLOMBO = timezone(timedelta(hours=5, minutes=30))
REPORTS = Path.home() / 'Documents' / 'ActivTrack Reports'
STATE = REPORTS / 'FCL Tracking' / 'dashboard_state.json'
TRACKING_LOCK = threading.Lock()

def normalized(value): return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
def carrier_name(value):
    text=str(value or '').upper()
    for words,name in [(('MSC','MEDITERRANEAN'),'MSC'),(('OOCL',),'OOCL'),(('COSCO',),'COSCO'),(('WAN HAI','WANHAI'),'Wan Hai'),(('EVERGREEN','EMC'),'Evergreen'),(('MAERSK',),'Maersk'),(('HAPAG',),'Hapag-Lloyd'),(('CMA CGM',),'CMA CGM'),(('YANG MING','YML'),'Yang Ming'),(('ONE LINE','OCEAN NETWORK'),'ONE Line')]:
        if any(w in text for w in words): return name
    return str(value or '')

# Owner/operator hints only. Unknown/leased prefixes never infer an ocean carrier.
PREFIXES={'OOLU':'OOCL','OOCU':'OOCL','CSNU':'COSCO','COSU':'COSCO','CCLU':'COSCO','WHSU':'Wan Hai','WHLU':'Wan Hai','MSCU':'MSC','MEDU':'MSC','MSDU':'MSC','MSKU':'Maersk','MRKU':'Maersk','MRSU':'Maersk','EGSU':'Evergreen','EMCU':'Evergreen','EGHU':'Evergreen','EISU':'Evergreen','EITU':'Evergreen','HLCU':'Hapag-Lloyd','HLBU':'Hapag-Lloyd','CMAU':'CMA CGM','CGMU':'CMA CGM','ONEU':'ONE Line','YMLU':'Yang Ming'}

def valid_container(value):
    if not re.fullmatch(r'[A-Z]{3}U\d{7}',value):return False
    # ISO 6346 skips values divisible by eleven in the alphabet mapping.
    values={c:10+i+(i+9)//10 for i,c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}
    total=sum((int(c) if c.isdigit() else values[c])*(2**i) for i,c in enumerate(value[:10]))
    return total%11%10==int(value[-1])

def extract_fcl(source):
    from container_history import load_history
    history=load_history().get('entries',{})
    schedules={str(s.get('id')):s for s in source.get('schedules') or []}
    records=[]
    for booking in (source.get('bookings') or [])+(source.get('incomplete_bookings') or []):
        if str(booking.get('shipment_type') or '').upper()!='FCL':continue
        schedule=booking.get('vessel_schedule') or schedules.get(str(booking.get('vessel_schedule_id'))) or {}
        legs=sorted(schedule.get('transshipments') or [],key=lambda l:int(l.get('leg_number') or 0))
        remaining=[l for l in legs if not l.get('ata')]
        current=(remaining or legs or [{}])[0]
        final=(legs or [{}])[-1]
        vessel=(current.get('vessel') or {}).get('name') or booking.get('last_Leg_vessel') or ''
        carrier=carrier_name((schedule.get('carrier') or {}).get('name'))
        operator=carrier
        for prefix,owner in [('MSC ','MSC'),('WAN HAI ','Wan Hai'),('EVER ','Evergreen'),('MAERSK ','Maersk'),('CMA CGM ','CMA CGM'),('OOCL ','OOCL'),('COSCO ','COSCO')]:
            if vessel.upper().startswith(prefix):operator=owner;break
        raw=str(booking.get('container_number') or '').upper()
        containers=list(dict.fromkeys(re.findall(r'(?<![A-Z0-9])[A-Z]{3}U\s*\d{6}\s*\d(?!\d)',raw)))
        if not containers:containers=['']
        for c in containers:
            c=re.sub(r'\s','',c)
            records.append({'key':str(booking.get('id') or booking.get('reference_number'))+'|'+c,'booking_id':booking.get('reference_number',''),'vs_id':schedule.get('reference_number') or booking.get('vessel_schedule_reference_number') or '', 'container':c,'container_valid':valid_container(c),'owner':PREFIXES.get(c[:4],''),'carrier':carrier,'operator':operator,'vessel':vessel,'voyage':current.get('voyage',''),'pol':(legs[0].get('departure_port') or {}).get('name','') if legs else booking.get('departure_port',''),'pod':(final.get('arrival_port') or {}).get('name') or schedule.get('arrival_port',''),'pol_code':(legs[0].get('departure_port') or {}).get('code','') if legs else '', 'pod_code':(final.get('arrival_port') or {}).get('code',''),'current_pol':(current.get('departure_port') or {}).get('name',''),'current_pol_code':(current.get('departure_port') or {}).get('code',''),'current_pod':(current.get('arrival_port') or {}).get('name',''),'mbl':booking.get('mbl_number',''),'hbl':booking.get('hbl_number',''),'etd':(legs[0].get('etd') if legs else booking.get('first_etd')) or '', 'eta':final.get('eta',''),'legs':legs,'priority':0 if any((l.get('vessel') or {}).get('name','').upper().startswith('MSC ') for l in legs) or operator=='MSC' else 1,'snapshot_arrived':bool(final.get('ata')),'customer':booking.get('customer_name','')})
            records[-1]['container_raw']=raw
            records[-1]['final_arrival_at']=final.get('ata') or schedule.get('ata') or ''
            recording=next((h for h in booking.get('container_recording_history',[]) if h.get('container')==c),None) or history.get(str(booking.get('reference_number') or booking.get('id'))+'|'+c,{})
            for field in ('recorded_date','recording_basis','first_seen_at','first_seen_source','booking_updated_date','previous_seen_at','previous_containers'):
                records[-1][field]=recording.get(field,'')
    return sorted(records,key=lambda r:(r['priority'],r['eta'] or '9999',r['container'],r['booking_id']))

def enrich_source(source):
    from container_history import annotate
    annotate(source)
    source['fcl_containers']=extract_fcl(source)
    source['fcl_capture']={'version':1,'containers':len({r['container'] for r in source['fcl_containers'] if r['container']}),'missing_container_bookings':sum(not r['container'] for r in source['fcl_containers'])}
    return source

def arrived_before_today(record,today=None):
    today=today or datetime.now(COLOMBO).date()
    value=record.get('final_arrival_at') or (record.get('arrived_at') if record.get('status')=='ARRIVED' else '') or ''
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).date()<today
    except (ValueError,TypeError):return False

def tracking_problem(checks):
    reasons=' '.join(c.get('reason','').lower() for c in checks)
    if 'verification' in reasons and 'required' in reasons:return 'VERIFY REQUIRED'
    if 'iso check' in reasons or 'container missing' in reasons:return 'MISSING / INVALID'
    if 'no information on container' in reasons or 'no carrier record' in reasons:return 'NO CARRIER RECORD'
    if 'disabled' in reasons:return 'MANUAL CHECK'
    if 'form not detected' in reasons:return 'TRACKER ERROR'
    if 'decoded' in reasons or 'route/event' in reasons:return 'REVIEW DATA'
    if 'failed' in reasons:return 'CHECK FAILED'
    if 'no supported tracker' in reasons:return 'UNSUPPORTED'
    return 'N/A'

def next_check(now):
    for delta in (0,1):
        for hour in (10,13,16):
            slot=(now+timedelta(days=delta)).replace(hour=hour,minute=0,second=0,microsecond=0)
            if slot>now:return slot

def due_slot(now,completed):
    slots=[now.replace(hour=h,minute=0,second=0,microsecond=0) for h in (10,13,16)]
    eligible=[s for s in slots if s<=now]
    if not eligible:return None
    latest=eligible[-1].isoformat()
    return latest if latest not in completed else None

def write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=str),encoding='utf-8');temporary.replace(path)

def load_state():
    try:return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {'completed_slots':[],'containers':[],'vessel_changes':[]}

def event_time(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=COLOMBO)
    except (ValueError,TypeError):return None

def evaluate(record,checks):
    """Use actual container-linked transport events only; estimates never prove arrival."""
    observed=[]; reasons=[]
    cutoff=event_time(record.get('etd'))
    now=datetime.now(COLOMBO)
    for check in checks:
        reasons.append(check.get('provider','')+': '+check.get('reason',''))
        for event in check.get('events',[]):
            dt=event_time(event.get('time'))
            if normalized(event.get('container'))!=normalized(record['container']) or not dt or dt>now+timedelta(minutes=5):continue
            if cutoff and dt<cutoff-timedelta(days=7 if event.get('event')=='LOAD' else 1):continue
            if event.get('classifier')!='ACT' or event.get('mode')!='VESSEL':continue
            observed.append(event)
    result={'status':tracking_problem(checks),'departure':'NOT CONFIRMED','reason':'; '.join(reasons),'checks':checks,'checked_at':now.isoformat(timespec='seconds'),'evidence':observed,'verified_sources':len({c.get('provider') for c in checks if c.get('events')})}
    if not observed:
        if any(c.get('events') for c in checks):result.update(status='REVIEW DATA',reason='Carrier events do not match this shipment date window or actual-event requirements; '+result['reason'])
        return result
    def port_matches(a,b):
        aa=normalized(a);bb=normalized(b).split('SRI')[0]
        return bool(aa and bb and (aa==bb or (len(aa)>=4 and (aa in bb or bb in aa))))
    def matches(e,field):return port_matches(e.get('port'),record.get(field)) or bool(e.get('port_code') and e['port_code']==record.get(field+'_code'))
    arrivals=[e for e in observed if e.get('event')=='ARRI' and matches(e,'pod')]
    departures=[e for e in observed if e.get('event')=='DEPA' and matches(e,'pol')]
    if departures:result['departure']='DEPARTED'
    # A port arrival is container evidence, independent of a vessel's general ATA.
    if arrivals:result.update(status='ARRIVED',reason='Actual container arrival at final POD',arrived_at=max(e['time'] for e in arrivals));return result
    current_departures=[e for e in observed if e.get('event')=='DEPA' and matches(e,'current_pol')]
    if not current_departures:
        loads=[e for e in observed if e.get('event')=='LOAD' and matches(e,'current_pol')]
        if loads:
            latest=max(loads,key=lambda e:event_time(e['time']))
            if normalized(latest.get('vessel'))==normalized(record.get('vessel')):
                def voyage_key(v):
                    match=re.search(r'\d+[NSEW]$',normalized(v))
                    return match.group(0).lstrip('0') if match else normalized(v)
                if latest.get('voyage') and record.get('voyage') and voyage_key(latest['voyage'])!=voyage_key(record['voyage']):
                    result.update(status='VOYAGE CHANGED',reason='Actual container loading shows voyage '+latest['voyage'])
                else:result.update(status='LOADED',reason='Container loaded on expected vessel; departure not yet confirmed')
            else:result.update(status='VESSEL CHANGED',reason='Actual container loading shows '+latest.get('vessel',''))
    if current_departures:
        latest=max(current_departures,key=lambda e:event_time(e['time']))
        same_time=[e for e in current_departures if event_time(e['time'])==event_time(latest['time'])]
        if len({normalized(e['vessel']) for e in same_time if e.get('vessel')})>1:
            result.update(status='REVIEW DATA',reason='Carrier sources disagree on the departing vessel; manual verification required');return result
        if not latest.get('vessel'):result['reason']='Departure recorded; vessel identity not returned';return result
        if normalized(latest['vessel'])!=normalized(record.get('vessel')):result.update(status='VESSEL CHANGED',reason='Actual container departure shows '+latest['vessel']);return result
        def voyage(v):
            value=normalized(v);suffix=re.search(r'\d+[NSEW]$',value)
            return suffix.group(0) if suffix else value
        if latest.get('voyage') and voyage(latest['voyage'])!=voyage(record.get('voyage')):result.update(status='VOYAGE CHANGED',reason='Actual container departure shows voyage '+latest['voyage']);return result
        result.update(status='IN TRANSIT',reason='Actual container departure on expected vessel')
    if observed and result['status']=='N/A':result.update(status='REVIEW DATA',reason='Actual carrier event received, but it does not confirm the expected POL/POD and vessel movement')
    return result

def add_fcl_sheet(workbook,records):
    from openpyxl.styles import Font,PatternFill,Alignment
    ws=workbook.create_sheet('FCL CARGO')
    fields=[('vs_id','VS ID'),('vessel','VESSEL'),('container','CONTAINER'),('status','STATUS'),('recorded_date','FIRST RECORDED DATE'),('first_seen_at','FIRST SEEN IN SNAPSHOT'),('recording_basis','RECORDING DATE BASIS'),('voyage','VOYAGE'),('pol','POL'),('pod','POD'),('departure','POL DEPARTURE'),('booking_id','BOOKING'),('operator','CARRIER SITE'),('owner','OWNER SITE'),('checked_at','LAST CHECKED'),('reason','EVIDENCE / ISSUE')]
    ws.append([label for key,label in fields])
    for r in records:
        ws.append([r.get(key,'') for key,label in fields])
        for c in ws[ws.max_row]:
            c.alignment=Alignment(wrap_text=True,vertical='center');c.font=Font(name='Calibri',size=11)
            if r.get('status')=='ARRIVED':c.fill=PatternFill('solid',fgColor='DCFCE7')
            elif r.get('departure')=='DEPARTED':c.fill=PatternFill('solid',fgColor='166534');c.font=Font(name='Calibri',color='FFFFFF')
        if r.get('status')=='IN TRANSIT':
            ws.cell(ws.max_row,4).font=Font(color='DC2626',bold=True)
            ws.cell(ws.max_row,4).fill=PatternFill('solid',fgColor='FEE2E2')
    for cell in ws[1]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='17365D')
    for col in 'ABCDEFGHIJKLMNOP':ws.column_dimensions[col].width={'B':28,'G':48,'P':65}.get(col,23)
    ws.freeze_panes='E2';ws.auto_filter.ref=ws.dimensions;ws.sheet_view.showGridLines=False
