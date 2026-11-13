"""Snapshot-backed container recording history, scoped to each booking."""
import json
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path.home()/'Documents'/'ActivTrack Reports'
HISTORY=ROOT/'FCL Tracking'/'container_history.json'
OVERRIDES=ROOT/'FCL Tracking'/'container_date_overrides.json'
SL=timezone(timedelta(hours=5,minutes=30))

def container_numbers(value):
    return list(dict.fromkeys(re.sub(r'\s','',v) for v in re.findall(r'(?<![A-Z0-9])[A-Z]{3}U\s*\d{6}\s*\d(?!\d)',str(value or '').upper())))

def snapshot_time(path,data):
    raw=(data.get('capture') or {}).get('captured_at')
    if raw:
        try:
            dt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
            return (dt if dt.tzinfo else dt.replace(tzinfo=SL)).astimezone(SL).isoformat(timespec='seconds')
        except ValueError:pass
    m=re.search(r'_(\d{8})_(\d{6}|\d{4})_Source',path.name)
    if m:
        dt=datetime.strptime(m[1]+m[2],'%Y%m%d%H%M%S' if len(m[2])==6 else '%Y%m%d%H%M')
        return dt.replace(tzinfo=SL).isoformat(timespec='seconds')
    return datetime.fromtimestamp(path.stat().st_mtime,SL).isoformat(timespec='seconds')

def load_history(path=HISTORY):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {'entries':{},'files':{}}

def refresh_history(folder=ROOT,output=HISTORY,overrides_path=OVERRIDES):
    paths=sorted(folder.glob('ActivTrack_Vessel_Booking_Report_*_Source.json'))
    manifest={str(p):[p.stat().st_mtime_ns,p.stat().st_size] for p in paths}
    try:overrides=json.loads(overrides_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):overrides=[]
    cached=load_history(output)
    if cached.get('files')==manifest and cached.get('overrides')==overrides:return cached
    old_files=cached.get('files',{})
    new_paths=[p for p in paths if str(p) not in old_files]
    incremental=bool(old_files and 'previous' in cached and all(manifest.get(k)==v for k,v in old_files.items()) and all(p.name>max(Path(k).name for k in old_files) for p in new_paths))
    entries=cached.get('entries',{}) if incremental else {};previous=cached.get('previous',{}) if incremental else {}
    for path in (new_paths if incremental else paths):
        try:data=json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):continue
        when=snapshot_time(path,data)
        for b in (data.get('bookings') or [])+(data.get('incomplete_bookings') or []):
            booking=str(b.get('reference_number') or b.get('id') or '')
            if not booking:continue
            containers=container_numbers(b.get('container_number'))
            for container in containers:
                key=booking+'|'+container
                if key not in entries or when<entries[key]['first_seen_at']:
                    prior=previous.get(booking,{})
                    entries[key]={'booking_id':booking,'container':container,'hbl':b.get('hbl_number') or '', 'so':b.get('so_number') or '', 'reference':b.get('ref_number') or '', 'shipment_type':b.get('shipment_type') or '', 'first_seen_at':when,'first_seen_source':str(path),'recorded_date':when[:10],'recording_basis':'First observed in snapshot; exact entry date/time unknown','booking_updated_date':b.get('updated_at') or '', 'previous_seen_at':prior.get('at','') if prior.get('at','')<when else '', 'previous_containers':prior.get('containers',[]) if prior.get('at','')<when else []}
            previous[booking]={'at':when,'containers':containers}
    for override in overrides:
        item=entries.get(str(override.get('booking_id'))+'|'+str(override.get('container')))
        if item and (not override.get('hbl') or item['hbl']==override['hbl']):
            item['recorded_date']=override['recorded_date'];item['recording_basis']='User-confirmed recording date; exact entry time unknown'
    result={'version':1,'files':manifest,'overrides':overrides,'entries':entries,'previous':previous}
    output.parent.mkdir(parents=True,exist_ok=True);tmp=output.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8');tmp.replace(output)
    return result

def note_saved_capture(path,history):
    history['files'][str(path)]=[path.stat().st_mtime_ns,path.stat().st_size]
    tmp=HISTORY.with_suffix('.tmp');tmp.write_text(json.dumps(history,indent=2,ensure_ascii=False),encoding='utf-8');tmp.replace(HISTORY)

def annotate(source,history=None):
    history=history or load_history()
    for b in (source.get('bookings') or [])+(source.get('incomplete_bookings') or []):
        ref=str(b.get('reference_number') or b.get('id') or '')
        b['container_recording_history']=[history['entries'][ref+'|'+c] for c in container_numbers(b.get('container_number')) if ref+'|'+c in history.get('entries',{})]
    return source


def current_entries(source):
    import copy
    source=copy.deepcopy(source)
    annotate(source)
    return [h for b in (source.get('bookings') or [])+(source.get('incomplete_bookings') or []) for h in b.get('container_recording_history',[])]

def add_history_sheet(workbook,source):
    from openpyxl.styles import Font,PatternFill,Alignment
    ws=workbook.create_sheet('CONTAINER HISTORY')
    fields=[('booking_id','BOOKING'),('reference','REFERENCE'),('hbl','HBL'),('container','CONTAINER'),('shipment_type','TYPE'),('recorded_date','FIRST RECORDED DATE'),('recording_basis','DATE BASIS'),('first_seen_at','FIRST SEEN IN SNAPSHOT'),('previous_seen_at','PREVIOUS BOOKING SNAPSHOT'),('previous_containers','PREVIOUS CONTAINERS'),('booking_updated_date','BOOKING UPDATED DATE'),('first_seen_source','EVIDENCE FILE')]
    ws.append([v for k,v in fields])
    for item in current_entries(source):
        ws.append([', '.join(item.get(k,[])) if isinstance(item.get(k),list) else item.get(k,'') for k,v in fields])
    for c in ws[1]:c.font=Font(color='FFFFFF',bold=True);c.fill=PatternFill('solid',fgColor='17365D')
    for row in ws.iter_rows(min_row=2):
        for c in row:c.alignment=Alignment(vertical='top',wrap_text=True)
    for col in 'ABCDEFGHIJKL':ws.column_dimensions[col].width={'G':56,'L':65}.get(col,25)
    ws.freeze_panes='E2';ws.auto_filter.ref=ws.dimensions;ws.sheet_view.showGridLines=False
