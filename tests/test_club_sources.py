import pytest
from app.models import SourceConfig
from app.parsers import extract

@pytest.mark.parametrize('url,html,count',[
 ('https://newyorkcomedyclub.com/open-mic','<p>Mondays 5pm at Midtown Tuesdays 4pm at East Village Wednesdays at 5pm at East Village Fridays at 4pm at Midtown $5 to perform 241 East 24th Street 85 East 4th Street</p>',4),
 ('https://comicstriplive.com/','<table><tr><td>Gladys Open Mics:</td><td>Wednesday / Friday 6.00 PM</td></tr><tr><td>DF Open Mic/Workshop:</td><td>Tuesday 6.00 PM</td></tr><tr><td>New Talent Show:</td><td>Saturday 5.00 PM</td></tr></table>',3),
 ('https://www.bkmadecomedy.com/','<p>OPEN MICS: MON - THURS 6 &amp; 8PM, FRI &amp; SAT 6PM | SHOWS: FRI &amp; SAT 8PM</p>',10),
 ('https://www.eastvillecomedy.com/pages/open-mics','<h2>OPEN MIC SCHEDULE</h2><p>MONDAY</p><p>First Open Mic: 6pm-7pm</p><p>Second Mic: 7pm-8pm</p><p>TUESDAY</p><p>Mecca Mic: 6pm-7:00</p><p>For updates subscribe</p>',3),
 ('https://qedastoria.com/collections/open-mic-nights','<p>GENERAL OPEN MIC SCHEDULE MON @ 6 pm WED @ 9:15 pm 1st SAT @ 3 pm - no stand up comedy ~~~~</p>',2),
 ('https://comedymob.com/','<p>Weekly Open Mics Weekly Lineup Doors = Mic Time SUN Sunday Mic New York Comedy Club — East 4th St 5:30 PM MON Monday Night Mob Rodney’s Comedy Club 7:00 PM THU Thursday Mic New York Comedy Club — 24th St 6:00 PM WED 10AM The list opens</p>',3),
 ('https://www.comedyshopnyc.com/location/comedy-shop-nyc/','<p>The Comedy Shop hosts open mics every day at 4pm &amp; 6pm.</p>',14),
])
def test_official_weekly_schedules(url,html,count):
 result=extract(html.encode(),SourceConfig(name='Club',url=url))
 assert len(result['rows'])==count
 assert result['skipped']==0
 assert len({r['remote_key'] for r in result['rows']})==count
 assert all(r['borough'] in ('Manhattan','Brooklyn','Queens') for r in result['rows'])

def test_layout_failure_does_not_return_empty_schedule():
 with pytest.raises(ValueError,match='review'):
  extract(b'<p>Tickets for tonight 8pm</p>',SourceConfig(name='Club',url='https://www.bkmadecomedy.com/'))

def test_biweekly_anchor_validation():
 from app.parsers import normalize_rows
 raw=dict(name='Mic',venue='Venue',borough='Queens',weekday='Tuesday',start_time='6pm',frequency='biweekly',recurrence_anchor='2026-09-29')
 rows,_,skipped=normalize_rows([raw],SourceConfig(name='Test'))
 assert not skipped and rows[0]['recurrence_anchor']=='2026-09-29'
 raw['recurrence_anchor']='2026-09-30'
 rows,warnings,skipped=normalize_rows([raw],SourceConfig(name='Test'))
 assert skipped==1 and not rows and 'weekday' in warnings[0]
