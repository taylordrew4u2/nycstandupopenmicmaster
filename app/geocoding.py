"""Cached US Census address geocoding. Approximate street-address matches are labeled.

No invented coordinates. Ambiguous/no matches remain unmapped for admin review.
Only a fixed government endpoint is queried; arbitrary URLs are never accepted.
"""
import asyncio
import hashlib
import os
import time
import aiohttp

def address_key(row):
    text='|'.join(str(row.get(k,'')).strip().lower() for k in ('address','borough'))
    return hashlib.sha256(text.encode()).hexdigest()

async def geocode_pending(store):
    if os.getenv('GEOCODING_ENABLED','1')!='1':
        return
    rows=store.public_data(include_hidden=True)['listings']
    pending=[]
    with store.connect() as c:
        for row in rows:
            if not row.get('address') or row.get('latitude') is not None:
                continue
            key=address_key(row)
            old=c.execute('SELECT * FROM locations WHERE address_key=?',(key,)).fetchone()
            if old and time.time()-old['checked']<7*86400:
                continue
            if not any(k==key for k,_ in pending):
                pending.append((key,row))
    timeout=aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout,headers={'User-Agent':'MicListNYC/1.0'},trust_env=False) as session:
        for key,row in pending[:8]:
            city='New York' if row['borough']=='Manhattan' else row['borough']
            query=f"{row['address']}, {city}, NY"
            lat,lng,label,state=None,None,'','unmatched'
            try:
                async with session.get('https://geocoding.geo.census.gov/geocoder/locations/onelineaddress',
                                       params={'address':query,'benchmark':'Public_AR_Current','format':'json'},allow_redirects=False) as response:
                    if response.status!=200:
                        raise ValueError('Geocoder unavailable')
                    data=await response.json()
                    matches=data.get('result',{}).get('addressMatches',[])
                    if len(matches)==1:
                        match=matches[0]
                        y,x=match['coordinates']['y'],match['coordinates']['x']
                        if 40.47<=y<=40.93 and -74.27<=x<=-73.68 and match.get('addressComponents',{}).get('state')=='NY':
                            lat,lng,label,state=y,x,match['matchedAddress'],'geocoded'
                    elif len(matches)>1:
                        state='ambiguous'
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,KeyError,TypeError):
                state='error'
            with store.connect() as c:
                c.execute('INSERT INTO locations VALUES (?,?,?,?,?,?) ON CONFLICT(address_key) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,state=excluded.state,checked=excluded.checked,label=excluded.label',
                          (key,lat,lng,state,time.time(),label))
            await asyncio.sleep(1)
