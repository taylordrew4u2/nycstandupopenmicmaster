import pytest
from app.venue_confirmation import official_venue_source
from app.models import SourceConfig
from app.parsers import normalize_rows
from app.store import Store

@pytest.mark.parametrize('url,expected',[
 ('https://www.bushwickcomedy.com/open-mics',True),
 ('https://comicstriplive.com/',True),
 ('https://comediq.us/mics.json',False),
 ('https://badslava.com/new-york-open-mics.php',False),
 ('https://comicstriplive.com.fake.example/',False),
 ('https://fake.example/?url=https://comicstriplive.com',False),
])
def test_official_domain_only(url,expected):
 assert official_venue_source(url)==expected

def test_venue_confirmation_does_not_claim_mic(database):
 store=Store(database)
 config=SourceConfig(name='Club',url='https://comicstriplive.com/',kind='json')
 rows=normalize_rows([{'name':'Club Mic','venue':'Club','borough':'Manhattan','weekday':'Monday','start_time':'19:00'}],config)
 source=store.commit_preview(store.preview(config.model_dump(),{'rows':rows[0],'warnings':rows[1],'skipped':rows[2]}))
 row=store.public_data()['listings'][0]
 assert row['venue_confirmed'] and not row['claimed']
 with store.connect() as c:
  c.execute('UPDATE observations SET missing_count=1 WHERE source_id=?',(source,))
 row=store.public_data()['listings'][0]
 assert not row['venue_confirmed'] and not row['hidden'] and not row['claimed']
