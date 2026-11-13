"""Quiet Edge container checks. Human challenges are reported, never bypassed.

Only recognized, actual, container-linked event schemas produce shipment status.
Unrecognized carrier responses stay available for manual verification.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from fcl_tracking import normalized, REPORTS

SITES={
    'MSC':'https://www.msc.com/en/track-a-shipment',
    'OOCL':'https://www.oocl.com/eng/ourservices/eservices/cargotracking/Pages/CargoTracking.aspx',
    'COSCO':'https://elines.coscoshipping.com/ebusiness/cargoTracking',
    'Wan Hai':'https://www.wanhai.com/views/cargoTrack/CargoTrack.xhtml',
    'Evergreen':'https://ct.shipmentlink.com/servlet/TDB1_CargoTracking.do',
    'Maersk':'https://www.maersk.com/tracking/',
    'Hapag-Lloyd':'https://www.hapag-lloyd.com/en/online-business/track/track-by-container-solution.html',
    'ONE Line':'https://ecomm.one-line.com/one-ecom/manage-shipment/cargo-tracking',
    'Yang Ming':'https://www.yangming.com/e-service/track_trace/track_trace_cargo_tracking.aspx',
}

def dcsa_events(payload,container,inherited_container=''):
    """Recognize DCSA 2.x transport events inside an explicit equipment context."""
    events=[]
    if isinstance(payload,list):
        for item in payload:events.extend(dcsa_events(item,container,inherited_container))
    elif isinstance(payload,dict):
        context=payload.get('equipmentReference') or payload.get('containerNumber') or inherited_container
        if payload.get('eventType')=='TRANSPORT' and payload.get('transportEventTypeCode') in ('ARRI','DEPA'):
            links=[r.get('referenceValue') for r in payload.get('references') or [] if isinstance(r,dict) and r.get('referenceType') in ('EQ','EQ_REFERENCE')]
            if normalized(context)==normalized(container) or any(normalized(v)==normalized(container) for v in links):
                call=payload.get('transportCall') or {};vessel=call.get('vessel') or {};location=call.get('location') or payload.get('eventLocation') or {}
                if payload.get('eventClassifierCode')=='ACT' and call.get('modeOfTransport') in ('VESSEL','1'):
                    events.append({'container':container,'classifier':'ACT','mode':'VESSEL','event':payload['transportEventTypeCode'],'time':payload.get('eventDateTime'),'port':location.get('locationName') or call.get('UNLocationCode') or '', 'port_code':call.get('UNLocationCode') or location.get('UNLocationCode') or '', 'vessel':vessel.get('vesselName',''),'voyage':call.get('exportVoyageNumber') or call.get('importVoyageNumber') or ''})
        for value in payload.values():
            if isinstance(value,(dict,list)):events.extend(dcsa_events(value,container,context))
    return events

def evergreen_events(tables,container):
    """Decode only exact-container rows in Evergreen's labelled current-status table."""
    from datetime import datetime
    events=[]
    for rows in tables:
        headers=None
        for row in rows:
            labels=[str(c).strip() for c in row]
            if 'Container No.' in labels and 'Container Moves' in labels:
                headers=labels;continue
            if not headers or len(row)!=len(headers):continue
            item=dict(zip(headers,labels))
            if normalized(item.get('Container No.'))!=normalized(container):continue
            move=item.get('Container Moves','').lower()
            if 'loaded' in move and 'on vessel' in move:kind='LOAD'
            elif 'discharg' in move and 'vessel' in move:kind='ARRI'
            elif 'depart' in move and 'vessel' in move:kind='DEPA'
            else:continue
            try:when=datetime.strptime(item.get('Date',''),'%b-%d-%Y').isoformat()
            except ValueError:continue
            vessel_voyage=item.get('Vessel Voyage','').strip()
            match=re.fullmatch(r'(.+?)\s+(\d[\w-]*[NSEW])',vessel_voyage,re.I)
            if not match:continue
            events.append({'container':container,'classifier':'ACT','mode':'VESSEL','event':kind,'time':when,'date_only':True,'port':re.sub(r'\s*\([A-Z]{2}\)\s*$','',item.get('Location','')),'vessel':match[1],'voyage':match[2],'source_move':item.get('Container Moves','')})
    return events

class ContainerBrowser:
    def __init__(self,stop):self.stop=stop;self.driver=None;self.provider=None
    def open(self,provider,visible=False):
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
        self.close();options=Options()
        if not visible:options.add_argument('--headless=new')
        options.add_argument('--window-size=1500,1000')
        profile=Path(os.getenv('LOCALAPPDATA',str(Path.home())))/'ActivTrackReportGenerator'/'FCLBrowserProfiles'/normalized(provider);profile.mkdir(parents=True,exist_ok=True)
        options.add_argument('--user-data-dir='+str(profile))
        options.page_load_strategy='eager';options.set_capability('ms:loggingPrefs',{'performance':'ALL'})
        self.driver=webdriver.Edge(options=options);self.driver.set_page_load_timeout(25);self.provider=provider
    def check(self,provider,container,visible=False):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        result={'provider':provider,'events':[],'url':SITES.get(provider,''),'reason':'No supported tracker'}
        if not result['url']:return result
        try:
            if not self.driver or self.provider!=provider or visible:self.open(provider,visible)
            if provider=='COSCO':
                result['url']='https://elines.coscoshipping.com/scct/public/ct/base?lang=en&trackingType=CONTAINER&number='+container
            self.driver.execute_cdp_cmd('Network.enable',{});self.driver.get(result['url'])
            if visible:
                # User completes the site challenge; never solve it automatically.
                for _ in range(60):
                    if self.stop.is_set():return dict(result,reason='Stopped')
                    time.sleep(1)
            ready=time.monotonic()+(18 if provider=='COSCO' else 8)
            body=''
            while time.monotonic()<ready and not self.stop.is_set():
                body=self.driver.find_element(By.TAG_NAME,'body').text
                if any(word in body.lower() for word in ('verify you are human','performing security verification','access denied','request unsuccessful','verify you are not a bot')):break
                if (provider=='COSCO' and container in body and 'Dynamic Node' in body) or (provider!='COSCO' and self.driver.find_elements(By.TAG_NAME,'input')):break
                time.sleep(.5)
            result['page_text']=body[:8000]
            if any(word in body.lower() for word in ('verify you are human','performing security verification','just a moment','access denied','unusual traffic','verify you are not a bot','request unsuccessful')):
                return dict(result,reason='Verification required — open verification from dashboard')
            if provider=='COSCO' and container in body:
                # COSCO's visible report is useful for review even when its
                # proprietary response cannot yet be converted to verified events.
                result['reason']='Carrier report received; route/event verification still required'
                return result
            if provider=='Evergreen':
                for button in self.driver.find_elements(By.ID,'btn_cookie_essential_only'):
                    if button.is_displayed():button.click()
                for radio_id in ('s_cntr','m_cntr'):
                    radios=self.driver.find_elements(By.ID,radio_id)
                    if radios and radios[0].is_displayed():radios[0].click();break
            inputs=[]
            for el in self.driver.find_elements(By.CSS_SELECTOR,'input:not([type=password]):not([type=hidden])'):
                label=' '.join(str(el.get_attribute(a) or '') for a in ('placeholder','aria-label','name','id')).lower()
                if el.is_displayed() and el.is_enabled() and (any(w in label for w in ('container','tracking','tracknumber','reference','equipment')) or (provider=='Evergreen' and el.get_attribute('name')=='NO')) and not any(w in label for w in ('email','password','user','searchsite','search-site')):inputs.append(el)
            if not inputs:return dict(result,reason='Tracker form not detected — manual check required')
            field=inputs[0];field.clear();field.send_keys(container)
            buttons=[b for b in self.driver.find_elements(By.CSS_SELECTOR,'button,input[type=submit],input[type=button]') if b.is_displayed() and b.is_enabled() and re.fullmatch(r'(track|track cargo|search|go|trace|track shipment|submit)',(b.text or b.get_attribute('value') or '').strip(),re.I)]
            if buttons:buttons[0].click()
            else:field.send_keys(Keys.ENTER)
            end=time.monotonic()+12;seen=set()
            while time.monotonic()<end and not self.stop.is_set():
                if provider=='Evergreen':
                    body=self.driver.find_element(By.TAG_NAME,'body').text
                    if 'No information on Container No' in body:
                        result['page_text']=body[:8000]
                        return dict(result,reason='No carrier record returned for this container')
                    tables=self.driver.execute_script("return Array.from(document.querySelectorAll('table')).map(t=>Array.from(t.rows).map(r=>Array.from(r.cells).map(c=>c.innerText)))")
                    result['events']=evergreen_events(tables,container)
                    if result['events']:break
                for entry in self.driver.get_log('performance'):
                    try:
                        message=json.loads(entry['message'])['message']
                        if message['method']!='Network.responseReceived':continue
                        response=message['params']['response'];req=message['params']['requestId']
                        if req in seen or 'json' not in response.get('mimeType',''):continue
                        seen.add(req)
                        host=urlsplit(response.get('url','')).hostname or '';base=urlsplit(result['url']).hostname or ''
                        # Read only responses from this carrier's domain family.
                        if '.'.join(host.split('.')[-2:])!='.'.join(base.split('.')[-2:]):continue
                        raw=self.driver.execute_cdp_cmd('Network.getResponseBody',{'requestId':req})
                        if raw.get('base64Encoded'):continue
                        result['events'].extend(dcsa_events(json.loads(raw['body']),container))
                    except (KeyError,ValueError,TypeError):continue
                    except Exception:continue
                if result['events']:break
                time.sleep(.5)
            result['page_text']=self.driver.find_element(By.TAG_NAME,'body').text[:8000]
            challenge=any(w in result['page_text'].lower() for w in ('verify you are human','verify you are not a bot','access denied','request unsuccessful','performing security verification'))
            result['reason']='Verification required — open verification from dashboard' if challenge else ('Actual container events received' if result['events'] else 'No verified container events decoded — manual check required')
            result['url']=self.driver.current_url
        except Exception as exc:
            result['reason']='Carrier check failed ('+type(exc).__name__+'); manual check required';self.close()
        return result
    def close(self):
        if self.driver:
            try:self.driver.quit()
            except Exception:pass
        self.driver=None;self.provider=None
