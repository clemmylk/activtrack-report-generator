import json,threading,unittest
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from polished_app import make_handler

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.actions=[]
        self.bridge=SimpleNamespace(token='test-session-secret',lock=threading.Lock(),state={'status':'Ready'},last_seen=0,started=False,root=SimpleNamespace(after=lambda delay,fn:fn()),action=lambda name,args:self.actions.append((name,args)),reports=lambda:{'rows':[{'vessel_name':'EXACT VESSEL','voyage':'002W'}]})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bridge))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):self.server.shutdown();self.server.server_close()
    def request(self,path,data=None,token='test-session-secret',origin=None):
        headers={'X-ActivTrack-Token':token}
        if origin:headers['Origin']=origin
        return urlopen(Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers),timeout=4)
    def test_authentication_required(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/state',token='wrong')
        self.assertEqual(ctx.exception.code,403)
    def test_state_and_reports_preserve_live_fields(self):
        with self.request('/api/state') as r:self.assertEqual(json.load(r)['status'],'Ready')
        with self.request('/api/reports') as r:self.assertEqual(json.load(r)['rows'][0]['voyage'],'002W')
        self.assertTrue(self.bridge.started)
    def test_action_dispatch(self):
        with self.request('/api/action',{'name':'capture','args':{'date':'2026-11-13'}},origin=self.base) as r:self.assertTrue(json.load(r)['queued'])
        self.assertEqual(self.actions,[('capture',{'date':'2026-11-13'})])
    def test_cross_origin_rejected(self):
        with self.assertRaises(HTTPError) as ctx:self.request('/api/action',{'name':'capture'},origin='http://127.0.0.1:1')
        self.assertEqual(ctx.exception.code,403);self.assertFalse(self.actions)
    def test_all_original_headers_available(self):
        with self.request('/api/headers') as r:self.assertEqual(len(json.load(r)),19)
    def test_notice_mixed_dates_and_missing_pairs(self):
        with self.request('/api/notice',{'row':{'at_etd':'2026-11-07','carrier_etd':'2026-11-08','at_eta':'2026-11-18','carrier_eta':'2026-11-17'}}) as r:
            notice=json.load(r);self.assertEqual(notice['header'],'ETD DELAYED AND ETA ADVANCED')
        self.assertFalse(self.actions)
        with self.assertRaises(HTTPError):self.request('/api/notice',{'row':{}})
    def test_static_bundle_available_and_traversal_denied(self):
        with self.request('/') as r:self.assertIn(b'ActivTrack',r.read())
        with self.assertRaises(HTTPError):self.request('/../main.py')

if __name__=='__main__':unittest.main()
