"""Read public vessel observations; reject stale positions and ambiguous names."""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

def compact(s): return re.sub('[^A-Z0-9]', '', s.upper())
def text(s): return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]*>', ' ', s))).strip()
def fetch(url, data=None):
    with urlopen(Request(url, data=data, headers={'User-Agent':'Mozilla/5.0', 'Content-Type':'application/json'}), timeout=12) as r:
        return r.read().decode('utf-8', errors='replace')

def resolve(name):
    page=fetch('https://www.vesselfinder.com/vessels?name='+quote(name))
    matches=set()
    for imo, block in re.findall(r'/vessels/details/(\d+)[^>]*>(.*?)</a', page, re.S):
        n=re.search(r'class="slna"[^>]*>(.*?)</div',block,re.S)
        if n and compact(text(n.group(1)))==compact(name) and len(imo)==7: matches.add(imo)
    if len(matches)!=1: raise ValueError('Vessel name needs identity review: multiple or no matches')
    return next(iter(matches))

def parse(page, name, imo):
    heading=re.search(r'<h1[^>]*>(.*?)</h1>',page,re.S)
    if not heading or compact(text(heading.group(1))) != compact(name):
        raise ValueError('Vessel identity could not be verified')
    cells={}
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',page,re.S):
        cols=re.findall(r'<td\b[^>]*>(.*?)</td>',row,re.S)
        if len(cols)>=2: cells[text(cols[0]).lower()]=text(cols[1])
    received=cells.get('position received','')
    age=re.search(r'(\d+)\s*(sec|min|hour|day)',received,re.I)
    seconds=int(age.group(1))*{'sec':1,'min':60,'hour':3600,'day':86400}[age.group(2).lower()] if age else None
    now=datetime.now(timezone.utc)
    source='https://www.vesselfinder.com/vessels/details/'+imo
    result={'status':'UNKNOWN','label':'Position stale or unavailable: '+received,'source_url':source,'checked_at':now.astimezone().isoformat(timespec='seconds'),'imo':imo}
    if seconds is None or seconds>7200: return result
    nav=cells.get('navigation status','').lower()
    status,label = ('MOORED','Moored (AIS; verify location)') if nav=='moored' else (('ANCHOR','At anchor') if 'anchor' in nav else (('UNDERWAY','Under way') if 'under way' in nav or 'underway' in nav else ('UNKNOWN','Navigation status unavailable')))
    intro=re.search(r'<p[^>]*class="[^"]*text2[^"]*"[^>]*>(.*?)</p>',page,re.S)
    result.update(status=status,label=label,ais_at=(now-timedelta(seconds=seconds)).isoformat(),port=text(intro.group(1))[:500] if intro else 'See source for current location')
    return result

def check_public(items):
    result={}
    for item in items:
        key=re.sub(r'\s+',' ',item['name'].strip().upper())
        try:
            imo=item.get('imo') or resolve(item['name'])
            result[key]=parse(fetch('https://www.vesselfinder.com/vessels/details/'+imo),item['name'],imo)
        except ValueError as exc: result[key]={'status':'UNKNOWN','label':str(exc)}
        except Exception: result[key]={'status':'UNKNOWN','label':'Public vessel source unavailable; manual check needed'}
    # SAGT current call list supplies terminal evidence, never infer an actual from ETA.
    try: calls=json.loads(json.loads(fetch('https://eport.sagt.com.lk/Default.aspx/VesselScheduleCallback',b'{}'))['d'])
    except Exception: calls=[]
    for item in items:
        key=re.sub(r'\s+',' ',item['name'].strip().upper())
        matches=[c for c in calls if compact(c.get('SHPNAME',''))==compact(item['name']) and not c.get('ATD')]
        if len(matches)!=1: continue
        call=matches[0]
        details=f"SAGT Colombo: ETA {call.get('ETA') or '-'}; ATA {call.get('ATA') or '-'}; reference {call.get('VESSELREF') or '-'}"
        if result[key]['status']=='UNKNOWN':
            result[key]['label']+='\n'+details
            if not call.get('ATA'): result[key]['display']='EXPECTED'
        result[key]['terminal_details']=details
    return result
