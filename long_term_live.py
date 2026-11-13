"""Serial carrier collection and future-sailing reconciliation."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from tracking_config import CARRIERS, BOT_ROOT, AUTO_TRACKING_DISABLED
from automated_tracking import route_bot, voyage_key
sys.path.insert(0,str(BOT_ROOT))
from carrier_bot_common import compact, port_key

def collect(rows, source, run_dir, stop, log, set_process):
    groups={bot:[] for bot in CARRIERS}; status={}; calls=[]
    for row in rows:
        carrier={'WHL':'Wan Hai','EMC':'Evergreen','CMA':'CMA CGM'}.get(row.get('carrier','').upper(),row.get('carrier',''))
        bot=route_bot(carrier,row['vessel'])
        if bot in AUTO_TRACKING_DISABLED:
            status[('MANUAL',compact(row['vessel']))]=bot+' automatic tracking temporarily disabled'
            bot=None
        row['bot']=bot or 'MANUAL'
        if bot in groups and compact(row['vessel']) not in {compact(v) for v in groups[bot]}:groups[bot].append(row['vessel'])
    for bot,names in groups.items():
        if not names or stop.is_set():continue
        script=CARRIERS[bot];log(f'{bot}: opening Edge for {len(names)} vessel(s).')
        env=os.environ.copy();env['TRACKING_VESSELS']='\n'.join(names);env['ACTIVTRACK_SOURCE_FILE']=str(source)
        process=None;folder=None
        try:
            process=subprocess.Popen([str(Path(sys.executable).with_name('python.exe')),'-u',str(script)],cwd=str(script.parent),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            set_process(process)
            if stop.is_set():process.terminate()
            for line in process.stdout:
                log(bot+': '+line.strip())
                if line.strip().startswith('Finished. Results:'):folder=Path(line.strip().split(':',1)[1].strip())
            code=process.wait()
            if code or not folder or not (folder/'results.json').is_file():raise RuntimeError('Carrier run failed or produced no results')
            target=run_dir/re.sub('[^A-Za-z0-9]','_',bot);shutil.copytree(folder,target,dirs_exist_ok=True)
            for record in json.loads((target/'results.json').read_text(encoding='utf-8-sig')):
                status[(bot,compact(record.get('vessel','')))]=record.get('status','NO DATA')
                for call in record.get('rows') or []:calls.append(dict(call,bot=bot))
        except Exception as exc:
            log(f'{bot}: {exc}')
            for name in names:status[(bot,compact(name))]='STOPPED' if stop.is_set() else 'BOT FAILED'
        finally:set_process(None)
    return calls,status

def call_date(call, field, target):
    value=call.get('carrier_'+field) or call.get('carrier_'+('atd' if field=='etd' else 'ata'))
    if value:
        try:return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:return ''
    value=call.get('departure_mmdd' if field=='etd' else 'arrival_mmdd')
    if not value:return ''
    try:
        ref=date.fromisoformat(target);m,d=map(int,str(value).split('/'))
        candidates=[]
        for year in (ref.year-1,ref.year,ref.year+1):
            try:candidates.append(date(year,m,d))
            except ValueError:pass
        closest=min(candidates,key=lambda x:abs((x-ref).days))
        return closest.isoformat() if abs((closest-ref).days)<=183 else ''
    except (ValueError,TypeError):return ''

def reconcile(baselines,calls,status):
    output=[]
    for row in baselines:
        r=dict(row,pasted_etd=row['etd'],pasted_eta=row['eta'],etd='',eta='',manual='',result='',source_url='')
        matching=[c for c in calls if compact(c.get('carrier_vessel',''))==compact(row['vessel']) and voyage_key(c.get('carrier_voyage',''))==voyage_key(row['voyage'])]
        for field,port in [('etd','pol'),('eta','pod')]:
            choices=[(call_date(c,field,row[field]),c) for c in matching if port_key(c.get('port',''))==port_key(row[port])]
            choices=[(v,c) for v,c in choices if v]
            values={v for v,c in choices}
            if len(values)==1:
                r[field]=next(iter(values));r['source_url']=choices[0][1].get('source_url','')
            elif len(values)>1:r['manual']+=field.upper()+' has multiple carrier calls; '
            else:r['manual']+=field.upper()+' not detected; '
        changes=[]
        if not r.get('vs'):r['manual']+='No matching ActiveTrack schedule in captured JSON; '
        else:
            for field in ('etd','eta'):
                if r[field] and r.get('at_'+field):
                    if r[field]!=r['at_'+field]:changes.append(field.upper()+(' DELAYED' if r[field]>r['at_'+field] else ' ADVANCED'))
                elif r[field]:changes.append(field.upper()+' MISSING IN ACTIVETRACK')
        r['result']='; '.join(changes) or 'NO CHANGE'
        if r['manual']:
            r['manual']+=status.get((row.get('bot','MANUAL'),compact(row['vessel'])),'Unsupported carrier or no exact sailing data')
        output.append(r)
    return output
