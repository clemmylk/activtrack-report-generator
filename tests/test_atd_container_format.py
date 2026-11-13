import tempfile,unittest
from pathlib import Path
from datetime import date
from openpyxl import load_workbook
from report_builder import build_report
class ATDContainerTests(unittest.TestCase):
 def test_vs_scoped_containers_fcl_bold_theme_fills_and_ata_unchanged(self):
  source={'bookings':[
   {'reference_number':'B-1','shipment_type':'FCL','vessel_schedule_id':1,'container_number':'TBA'},
   {'reference_number':'B-2','shipment_type':'LCL','vessel_schedule_id':2,'container_number':'EGHU8335111'},
   {'reference_number':'B-3','shipment_type':'FCL','vessel_schedule_id':3,'container_number':'EGHU8335111; MSCU1234567'},
   {'reference_number':'B-4','shipment_type':'LCL','vessel_schedule_id':3,'container_number':'EGHU8335111'},
   {'reference_number':'B-5','shipment_type':'LCL','vessel_schedule_id':4,'container_number':'N/A'}],
   'schedules':[{'id':n,'reference_number':f'VS-{n}','route_type':'transshipment' if n==3 else 'direct','legs':[{'vessel_name':'SAME VESSEL','voyage':'001W','etd':'2026-11-01','eta':'2026-11-03'}]} for n in range(1,5)]}
  with tempfile.TemporaryDirectory() as folder:
   target=Path(folder)/'report.xlsx';build_report(source,target,date(2026,11,9));wb=load_workbook(target)
   atd=wb['ATD Missing'];self.assertEqual(atd['R7'].value,'Container Number')
   by_vs={atd.cell(r,1).value:r for r in range(8,atd.max_row+1)}
   for vs,bold,theme,tint,container in [('VS-1',True,5,.6,None),('VS-2',False,6,.4,'EGHU8335111'),('VS-3',True,6,.4,'EGHU8335111, MSCU1234567'),('VS-4',False,5,.6,None)]:
    r=by_vs[vs];self.assertEqual(atd.cell(r,18).value,container)
    for cell in atd[r][:18]:
     self.assertEqual(cell.font.bold,bold)
     if cell.column == 1:
      self.assertEqual(cell.fill.fgColor.type,'theme');self.assertEqual(cell.fill.fgColor.theme,theme);self.assertAlmostEqual(cell.fill.fgColor.tint,tint)
     else:
      self.assertNotEqual(cell.fill.fgColor.type,'theme')
   self.assertEqual(len(atd.conditional_formatting),0)
   self.assertEqual(atd.tables['ATDMissingScheduleLegsTable'].ref,'A7:R11')
   self.assertIn('$Q$2',atd['K8'].value);self.assertEqual(atd['I8'].number_format,'yyyy-mm-dd')
   self.assertEqual(wb['ATA Missing'].max_column,17);self.assertNotIn('Container Number',[c.value for c in wb['ATA Missing'][7]])
   wb.close()
if __name__=='__main__':unittest.main()
