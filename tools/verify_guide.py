"""Compare computed show start times with the site's 'next 12 hours' guide.
usage: verify_guide.py <channel> <UTC ISO time the page was read> <comma list of the guide's start times>
e.g.   verify_guide.py ray-gun 2026-10-31T14:13:53 3:28,3:44,4:01
(times are in the +01:00 zone this was written in; edit tz below if yours differs)"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import schedule as S, datetime
chan=sys.argv[1]; t0=sys.argv[2]; site=sys.argv[3].split(',')
c=os.path.expanduser('~/.cache/spelunkr')
lineup,_=S.fetch('lineup.json',c); ch=next(x for x in lineup['channels'] if x['id']==chan)
man={v['v']:S.fetch(v['manifest'],c)[0] for v in ch['versions']}; pl={v:S.fetch(m['platform'],c)[0] for v,m in man.items()}
t=int(datetime.datetime.fromisoformat(t0+'+00:00').timestamp()*1000)
tz=datetime.timezone(datetime.timedelta(hours=1)); out=[]
for i in range(400):
    r=S.now_playing(ch,man,pl,t); e=r['entry']
    if e['type']=='video' and r['start_ms']>t-1000*3600*5:
        out.append(datetime.datetime.fromtimestamp(r['start_ms']/1000,tz).strftime('%-I:%M'))
    t=int(r['end_ms'])+1
    if len(out)>len(site)+1: break
mine=out[1:1+len(site)]
bad=[(i,a,b) for i,(a,b) in enumerate(zip(site,mine)) if a!=b]
print(chan,'compared',len(site),'mismatches',bad)
