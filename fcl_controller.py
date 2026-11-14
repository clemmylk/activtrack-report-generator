"""One background queue, persisted observations, Colombo-time scheduled rounds."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from fcl_tracking import COLOMBO, STATE, REPORTS, TRACKING_LOCK, extract_fcl, evaluate, load_state, write_json, due_slot, next_check,arrived_before_today
from container_history import refresh_history,annotate
from container_browser import ContainerBrowser
from tracking_report import latest_booking_source

class FCLController:
    def __init__(self,source_getter,browser_busy=lambda:False):
        self.source_getter=source_getter;self.browser_busy=browser_busy;self.state=load_state()
        self.stop=threading.Event();self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='fcl-background')
        self.busy=False;self.queued=False;self.message='Ready';self.progress=(0,0);self.last_error='';self._guard=threading.Lock();self.closed=False
    def source(self):
        value=self.source_getter()
        return Path(value) if value and Path(value).is_file() else latest_booking_source()
    def snapshot(self):return copy.deepcopy(self.state)
    def load_queue(self):
        path=self.source();data=json.loads(path.read_text(encoding='utf-8-sig'));annotate(data,refresh_history());all_records=extract_fcl(data)
        previous={r['key']:r for r in self.state.get('containers',[])}
        for r in all_records:
            old=previous.get(r['key'],{})
            if old.get('pod')==r.get('pod') and old.get('etd')==r.get('etd') and old.get('status')=='ARRIVED':
                r.update(status='ARRIVED',arrived_at=old.get('arrived_at',''))
        records=[r for r in all_records if not arrived_before_today(r)]
        self.state['excluded_past_arrivals']=len(all_records)-len(records)
        previous={r['key']:r for r in self.state.get('containers',[])}
        for r in records:
            old=previous.get(r['key'],{})
            if old.get('checked_at') and all(old.get(k)==r.get(k) for k in ('container','vessel','voyage','pol','pod')):
                for k in ('status','departure','reason','checked_at','checks','evidence','arrived_at'):r[k]=old.get(k,'')
            else:r.update(status='NOT CHECKED',departure='NOT CONFIRMED',reason='Not checked against this capture',checks=[])
            if r.get('checks') and r.get('status')=='N/A':
                from fcl_tracking import tracking_problem
                r['status']=tracking_problem(r['checks'])
            r['source_path']=str(path);r['source_mtime']=path.stat().st_mtime
        self.state['containers']=records;self.state['source']=str(path)
        return path,records
    def start(self,slot=None,verification=None):
        if self.closed or self.busy or self.queued:return False
        if self.browser_busy():self.message='Waiting for the current vessel tracking run to finish';return False
        self.queued=True
        self.executor.submit(self.run_sync,None,slot,verification);return True
    def run_sync(self,source_path=None,slot=None,verification=None):
        if not self._guard.acquire(False):return []
        self.queued=False;self.busy=True;self.stop.clear();browser=ContainerBrowser(self.stop)
        try:
            with TRACKING_LOCK:
                if source_path:
                    original_getter=self.source_getter
                    try:
                        self.source_getter=lambda:source_path
                        path,records=self.load_queue()
                    finally:self.source_getter=original_getter
                else:path,records=self.load_queue()
                if verification:
                    records=[r for r in records if r['key']==verification[0]]
                self.progress=(0,len(records));cache={};result=[]
                for index,record in enumerate(records,1):
                    if self.stop.is_set():break
                    self.message=f"{index}/{len(records)} — {record['container'] or record['booking_id']} — {'MSC PRIORITY' if record['priority']==0 else record['operator']}"
                    checks=[]
                    if not record['container_valid']:
                        checks=[{'provider':'Capture','reason':'Container missing or ISO check digit invalid','events':[]}]
                    else:
                        sites=list(dict.fromkeys(p for p in [record['operator'],record['owner'],record['carrier']] if p))
                        for provider in sites:
                            if self.stop.is_set():break
                            key=(provider,record['container'])
                            if key not in cache:
                                if provider=='CMA CGM':cache[key]={'provider':provider,'reason':'Automatic CMA CGM checks temporarily disabled','events':[]}
                                else:cache[key]=browser.check(provider,record['container'],visible=bool(verification and provider==verification[1]))
                            checks.append(cache[key])
                        if not record['owner']:checks.append({'provider':'Owner','reason':'Owner unknown or leasing prefix; second source unavailable','events':[]})
                        elif record['owner']==record['operator']:checks.append({'provider':'Owner','reason':'Same website as carrier; not an independent second source','events':[]})
                    observation=evaluate(record,checks)
                    result.append(dict(record,**observation,source_path=str(path),source_mtime=path.stat().st_mtime))
                    self.progress=(index,len(records))
                    # Save partial progress so stopping never loses completed observations.
                    current={r['key']:r for r in self.state.get('containers',[])}
                    current[record['key']]=result[-1];self.state['containers']=list(current.values());self.state['source']=str(path)
                    write_json(STATE,self.state)
                now=datetime.now(COLOMBO);self.state['last_round']=now.isoformat(timespec='seconds');self.state['stopped']=self.stop.is_set()
                if slot and not self.stop.is_set():self.state.setdefault('completed_slots',[]).append(slot)
                self.state['completed_slots']=self.state.get('completed_slots',[])[-12:]
                write_json(STATE,self.state)
                stamp=now.strftime('%Y%m%d_%H%M%S');write_json(REPORTS/'FCL Tracking'/'Runs'/f'FCL_{stamp}.json',{'source':str(path),'checked_at':now.isoformat(),'rows':result,'stopped':self.stop.is_set()})
                verified=sum(r['status'] in ('ARRIVED','IN TRANSIT','LOADED','VESSEL CHANGED','VOYAGE CHANGED') for r in result)
                self.message='Stopped — completed results saved' if self.stop.is_set() else f'Checked {len(result)} records: {verified} confirmed; {len(result)-verified} require attention. Past arrivals excluded: {self.state.get("excluded_past_arrivals",0)}'
                return result
        except Exception as exc:
            self.last_error=type(exc).__name__+': '+str(exc);self.message='Check needs attention: '+self.last_error
            return []
        finally:browser.close();self.busy=False;self._guard.release()
    def scheduled_tick(self):
        # Never start network tracking from the clock or dashboard navigation.
        return
    def close(self):
        self.closed=True;self.stop.set();self.executor.shutdown(wait=False,cancel_futures=True)
