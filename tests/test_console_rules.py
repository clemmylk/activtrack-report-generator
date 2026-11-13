import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from openpyxl import load_workbook
from console_rules import CONSOLE_AGENTS, CONSOLE_FILL, COLOAD_FILL, booking_class
from report_builder import build_report
from automated_tracking import classify_console_bookings

class ConsoleRulesTests(unittest.TestCase):
    def test_agents_and_lcl_only(self):
        for agent in CONSOLE_AGENTS:
            b = {"shipment_type": "LCL", "agent_name": agent.lower() + ".\\", "liner_co_loader_name": "EXPLICIT OTHER"}
            self.assertEqual(booking_class(b), "CONSOLE")
            self.assertEqual(classify_console_bookings([b])["priority_type"], "CONSOLE")
            self.assertEqual(booking_class({**b, "shipment_type": "FCL"}), "")
        self.assertEqual(booking_class({"shipment_type":"LCL", "agent":{"name":"OTHER AGENT"}}), "CO-LOAD")
        self.assertEqual(booking_class({"shipment_type":"LCL"}), "UNCLASSIFIED LCL")

    def test_workbook_booking_and_route_identity(self):
        source = {"bookings":[
            {"reference_number":"B-1", "shipment_type":"LCL", "agent_name":CONSOLE_AGENTS[0], "vessel_schedule_id":1, "hbl_number":"HOUSE1"},
            {"reference_number":"B-2", "shipment_type":"LCL", "agent_name":"OTHER", "vessel_schedule_id":2},
            {"reference_number":"B-3", "shipment_type":"FCL", "agent_name":CONSOLE_AGENTS[0], "vessel_schedule_id":2},
        ], "schedules":[{"id":i,"reference_number":f"VS-{i}","legs":[{"vessel_name":"SAME VESSEL","voyage":"001W","etd":"2026-11-14","eta":"2026-11-18"}]} for i in (1,2)]}
        with TemporaryDirectory() as folder:
            p=Path(folder)/"report.xlsx"
            build_report(source,p,date(2026,11,13))
            wb=load_workbook(p)
            self.assertEqual(wb.sheetnames[1], "Console & Co-load")
            sheet=wb.worksheets[1]
            self.assertEqual(sheet.max_row,3)
            self.assertEqual(sheet["H2"].value,"HOUSE1")
            self.assertEqual(sheet["A2"].fill.fgColor.rgb,"00"+CONSOLE_FILL)
            self.assertEqual(sheet["A3"].fill.fgColor.rgb,"00"+COLOAD_FILL)
            self.assertEqual(sheet.freeze_panes,"C2")
            self.assertEqual(wb["Booking Vessel Details"]["A2"].fill.fgColor.rgb,"00"+CONSOLE_FILL)
            self.assertNotEqual(wb["Booking Vessel Details"]["A4"].fill.fgColor.rgb,"00"+CONSOLE_FILL)
            wb.close()

    def test_atd_container_cell_does_not_clash_with_console_and_coload(self):
        source = {"bookings":[
            {"reference_number":"B-1", "shipment_type":"LCL", "agent_name":CONSOLE_AGENTS[0], "vessel_schedule_id":1, "container_number":"EGHU8335111"},
            {"reference_number":"B-2", "shipment_type":"LCL", "agent_name":"OTHER", "vessel_schedule_id":2},
        ], "schedules":[{"id":i,"reference_number":f"VS-{i}","legs":[{"vessel_name":"VESSEL","etd":"2026-11-01","eta":"2026-11-18"}]} for i in (1,2)]}
        with TemporaryDirectory() as folder:
            p=Path(folder)/"report.xlsx"
            build_report(source,p,date(2026,11,13))
            wb=load_workbook(p)
            ws=wb["ATD Missing"]
            for r,theme,color in [(8,6,CONSOLE_FILL),(9,5,COLOAD_FILL)]:
                self.assertEqual(ws.cell(r,1).fill.fgColor.type,"theme")
                self.assertEqual(ws.cell(r,1).fill.fgColor.theme,theme)
                for cell in ws[r][1:18]:
                    self.assertEqual(cell.fill.fgColor.rgb,"00"+color)
            wb.close()

    def test_live_transshipment_vessel_objects_and_reference_link(self):
        source={'bookings':[{'reference_number':'B-LIVE','shipment_type':'LCL','agent_name':CONSOLE_AGENTS[0],'vessel_schedule_reference_number':'VS-88'}],
                'schedules':[{'id':88,'reference_number':'VS-88','transshipments':[
                    {'vessel':{'id':1,'name':'FIRST VESSEL'},'voyage':'001W','departure_port':{'name':'Shanghai'},'arrival_port':{'name':'Hong Kong'}},
                    {'vessel':{'id':2,'name':'SECOND VESSEL'},'voyage':'002W','departure_port':{'name':'Hong Kong'},'arrival_port':{'name':'Colombo'}}]}]}
        with TemporaryDirectory() as folder:
            p=Path(folder)/'live.xlsx';build_report(source,p,date(2026,11,13));wb=load_workbook(p)
            sheet=wb['Console & Co-load']
            self.assertEqual(sheet.max_row,3)
            self.assertEqual((sheet['B2'].value,sheet['C2'].value,sheet['P2'].value),('FIRST VESSEL','001W','1/2'))
            self.assertEqual((sheet['B3'].value,sheet['C3'].value,sheet['P3'].value),('SECOND VESSEL','002W','2/2'))
            self.assertEqual(sheet['L2'].value,'Shanghai')
            wb.close()

if __name__ == "__main__":
    unittest.main()
