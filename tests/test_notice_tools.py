import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from notice_tools import HEADERS, build_notice, NoticeTools
from operations_dashboard import OperationsDashboard

class NoticeTests(unittest.TestCase):
    def row(self, **changes):
        return {"vs_id":"VS-1","vessel":"TEST VESSEL","voyage":"001W","leg":1,
                "pol":"Shanghai, China","pod":"Colombo, Sri Lanka",
                "at_etd":"2026-11-07","carrier_etd":"2026-11-08",
                "at_eta":"2026-11-18","carrier_eta":"2026-11-17",**changes}

    def test_mixed_direction_dates_and_no_invented_reason(self):
        payload=build_notice(self.row())
        self.assertEqual(payload['header'],'ETD DELAYED AND ETA ADVANCED')
        self.assertIn('DELAYED BY 1 DAY',payload['remarks'])
        self.assertIn('ADVANCED BY 1 DAY',payload['remarks'])
        self.assertNotIn('DUE TO',payload['remarks'])
        self.assertIn('SHANGHAI, CHINA',payload['remarks'])

    def test_same_direction_header_and_rollover_days(self):
        p=build_notice(self.row(at_eta='2026-12-31',carrier_eta='2027-01-02'))
        self.assertEqual(p['header'],'ETD AND ETA CHANGED AND DELAYED')
        self.assertIn('DELAYED BY 2 DAYS',p['remarks'])

    def test_second_leg_and_unknown_dates(self):
        p=build_notice(self.row(leg=2,at_etd=None,carrier_etd=None))
        self.assertEqual(p['header'],'SECOND LEG VESSEL ETA CHANGED AND ADVANCED')
        self.assertNotIn('ETD:',p['remarks'])
        self.assertNotIn('NONE',p['remarks'])

    def test_no_change_or_missing_pair_not_copyable(self):
        for row in (self.row(carrier_etd=None,carrier_eta=None),self.row(carrier_etd='2026-11-07',carrier_eta='2026-11-18')):
            with self.assertRaises(ValueError):build_notice(row)

    def test_first_leg_is_resolved_without_mutating_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.json'
            path.write_text(json.dumps({'schedules':[{'reference_number':'VS-1','legs':[{},{}]}]}))
            dashboard=OperationsDashboard.__new__(OperationsDashboard)
            dashboard.controller=SimpleNamespace(source=lambda:path)
            row=self.row()
            snapshot=dashboard.notice_row(row)
            self.assertNotIn('total_legs',row)
            self.assertEqual(build_notice(snapshot)['header'],'FIRST LEG VESSEL ETD DELAYED AND ETA ADVANCED')

    def test_optional_reason_and_vessel_change(self):
        p=build_notice(self.row(revised_vessel='REVISED',revised_voyage='002W'),'due to port congestion.')
        self.assertTrue(p['header'].startswith('VESSEL CHANGE,'))
        self.assertIn('DUE TO PORT CONGESTION.',p['remarks'])
        self.assertIn('REVISED / 002W',p['remarks'])

    def test_all_original_header_presets_retained(self):
        self.assertEqual(len(HEADERS),19)
        self.assertEqual(HEADERS[0][1],'ETA CHANGED AND DELAYED')
        self.assertEqual(HEADERS[-1][1],'SECOND LEG VESSEL ARRIVED')

    def test_native_builder_opens_and_populates_preview(self):
        import tkinter as tk
        root=tk.Tk();root.withdraw()
        try:
            tool=NoticeTools(root,self.row())
            tool.window.withdraw();root.update_idletasks()
            self.assertIsNotNone(tool.payload)
            self.assertIn('ETD DELAYED AND ETA ADVANCED',tool.preview.get('1.0','end'))
            tool.window.destroy()
        finally:root.destroy()

    def test_context_menu_copies_clicked_snapshot_not_later_selection(self):
        commands={}
        class Menu:
            def __init__(self,*args,**kwargs):pass
            def add_command(self,label,command):commands[label]=command
            def add_separator(self):pass
            def tk_popup(self,*args):pass
            def grab_release(self):pass
        dashboard=OperationsDashboard.__new__(OperationsDashboard)
        dashboard.window=object()
        dashboard.changes=SimpleNamespace(identify_row=lambda y:'selected',identify_region=lambda x,y:'cell',selection_set=lambda iid:None)
        dashboard.vessels={'selected':self.row()}
        dashboard.notice_row=lambda row:dict(row)
        dashboard.message=SimpleNamespace(set=lambda value:None)
        event=SimpleNamespace(x=10,y=10,x_root=10,y_root=10)
        copied=[]
        with patch('operations_dashboard.tk.Menu',Menu),patch('notice_tools.copy_text',lambda window,text:copied.append(text)):
            dashboard.notice_menu(event)
            dashboard.vessels.clear()
            commands['Copy header']();commands['Copy remarks']()
        self.assertEqual(copied[0],'ETD DELAYED AND ETA ADVANCED')
        self.assertIn('07 NOV 2026 → 08 NOV 2026',copied[1])

    def test_main_app_side_menu_opens_integrated_tool(self):
        import tkinter as tk
        from main import ReportApp
        root=tk.Tk();root.withdraw()
        app=None
        try:
            app=ReportApp(root)
            app._toggle_tools_menu()
            self.assertTrue(app.tools_menu.winfo_exists())
            buttons=[w for w in app.tools_menu.winfo_children() if w.winfo_class()=='TButton']
            notice_button=next(w for w in buttons if w.cget('text')=='Notice Headers & Remarks')
            notice_button.invoke()
            app.notice_tools.window.withdraw()
            root.update_idletasks()
            self.assertTrue(app.notice_tools.window.winfo_exists())
            self.assertFalse(app.tools_menu.winfo_exists())
        finally:
            if app:app._on_close()
            else:root.destroy()

if __name__=='__main__':unittest.main()
