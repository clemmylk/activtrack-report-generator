import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from main import ReportApp
from desktop_ui import FOOTER

class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.app=ReportApp(self.root)
    def tearDown(self):
        self.app._on_close()
    def test_approved_chrome_and_report_identity(self):
        ui=self.app.ui
        self.assertEqual(ui.active_page,'overview')
        self.assertEqual(ui.title.get(),'Daily Operations')
        self.assertEqual(ui.nav['overview'].cget('background'),'#E52329')
        self.assertIn('developed by Clement R.',FOOTER)
        self.assertTrue(hasattr(ui,'logo'))
        source={'bookings':[{'reference_number':'B-1','shipment_type':'LCL','agent_name':'ECU WORLDWIDE (HONG KONG) LIMITED','vessel_schedule_id':1}], 'schedules':[{'id':1,'reference_number':'VS-1','legs':[{'vessel_name':'LEG ONE','etd':'2026-11-01','eta':'2026-11-05'},{'vessel_name':'LEG TWO','etd':'2026-11-06','eta':'2026-11-14'}]}]}
        ui.render_report(source)
        self.assertEqual(len(ui.report_trees['console'].get_children()),2)
        self.assertEqual(len(ui.report_trees['summary'].get_children()),2)
        self.assertEqual(ui.report_trees['console'].item(ui.report_trees['console'].get_children()[0],'tags'),('console',))
    def test_manual_and_longterm_are_embedded_and_preserved(self):
        ui=self.app.ui
        with patch('tracking_window.TrackingWindow._refresh_source_label'):
            ui.tracking_mode('manual')
        runner=self.app.tracking_window
        self.assertTrue(runner.embedded)
        self.assertEqual(runner.window.winfo_class(),'Frame')
        ui.tracking_mode('longterm')
        self.assertTrue(self.app.long_term_window.embedded)
        self.app.long_term_window.editor.insert('1.0','User pasted schedule')
        ui.tracking_mode('auto');ui.tracking_mode('longterm')
        self.assertEqual(self.app.long_term_window.editor.get('1.0','end').strip(),'User pasted schedule')
        self.assertIs(ui.runners['manual'],runner)
    def test_automatic_starts_only_on_action_and_uses_locked_source(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'source.json';p.write_text('{}')
            self.app.morning_source_path=p
            from automated_tracking import BOT_ORDER
            with patch('automated_tracking_window.build_tracking_plan',return_value=([],{bot:[] for bot in BOT_ORDER},{})),patch('automated_tracking_window.trico_48h_rows',return_value=[]),patch('automated_tracking_window.AutomatedTrackingWindow._start') as start:
                self.app.ui.show('tracking')
                self.assertIsNone(self.app.automated_tracking_window)
                self.app._open_automated_tracking()
                runner=self.app.automated_tracking_window
                self.assertTrue(runner.embedded)
                self.assertEqual(runner.source_path,p)
                self.assertFalse(start.called)
                # Constructor queues a run; no real carrier bot is started in this test.
                runner.busy=False
    def test_embedded_dashboard_keeps_identity_and_rightclick_actions(self):
        controller=self.app.fcl_controller
        controller.load_queue=lambda:None
        with patch('operations_dashboard.bootstrap_vessel_results'),patch('operations_dashboard.OperationsDashboard.tick'):
            self.app._open_dashboard()
        dash=self.app.dashboard
        self.assertTrue(dash.embedded)
        sample={'vs_id':'VS-1','vessel':'EVER TEST','voyage':'1W','leg':1,'priority_type':'CONSOLE','at_etd':'2026-11-01','carrier_etd':'2026-11-02','at_eta':'2026-11-15','carrier_eta':'2026-11-14','result':'ETD DELAYED 1 DAY; ETA ADVANCED 1 DAY'}
        dash.render({'containers':[]},{'rows':[sample]})
        self.assertEqual(len(dash.change_identity.get_children()),1)
        self.assertEqual(len(dash.changes.get_children()),1)
        self.assertNotIn('vs',dash.changes.cget('displaycolumns'))
        dash.changes.xview_moveto(1)
        self.assertEqual(dash.change_identity.item('0','values')[0],'VS-1')
        self.assertTrue(dash.change_identity.bind('<Button-3>'))
        self.assertEqual(dash.views.index('current'),1)

if __name__=='__main__':unittest.main()
