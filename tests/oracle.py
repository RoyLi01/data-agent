"""Independent Python reference over source rows; does not call production SQL compiler."""
import sqlite3
from collections import defaultdict
from datetime import date,timedelta

def calculate(root,c,watermark='2026-09-20'):
    db=sqlite3.connect(root/'warehouse.sqlite');db.row_factory=sqlite3.Row
    tables={t:[dict(r) for r in db.execute('SELECT * FROM '+t)] for t in ['users','activations','activity','spend','channels']};db.close()
    channels={r['channel_id']:r for r in tables['channels']};users={r['user_id']:r for r in tables['users']}
    success=defaultdict(set)
    for a in tables['activations']:
        if a['status']=='success':success[a['user_id']].add(a['event_date'])
    active={(a['user_id'],a['event_date']) for a in tables['activity']}
    groups={};cost=defaultdict(float)
    def allowed(channel):
        ch=channels[channel]
        return (not c.channel or ch['channel_name']==c.channel) and (not c.category or ch['category']==c.category)
    def key(u,day):
        values={'date':day,'channel':channels[u['channel_id']]['channel_name'],'os':u.get('os')}
        return tuple(values[d] for d in c.dimensions)
    def add(k,uid,hit):
        g=groups.setdefault(k,{'denom':set(),'num':set()});g['denom'].add(uid)
        if hit:g['num'].add(uid)
    for u in users.values():
        if not allowed(u['channel_id']):continue
        if c.metric in ['activations','cpa']:
            for day in success[u['user_id']]:
                if str(c.start)<=day<=str(c.end):add(key(u,day),u['user_id'],True)
        else:
            day=u['registration_date'];d=date.fromisoformat(day)
            if not str(c.start)<=day<=str(c.end):continue
            maturity=7 if c.metric=='retention_d7' else 6 if c.metric=='activation_rate' else 0
            if d+timedelta(days=maturity)>date.fromisoformat(watermark):continue
            hit=True
            if c.metric=='retention_d7':hit=(u['user_id'],str(d+timedelta(days=7))) in active
            if c.metric=='activation_rate':hit=any(d<=date.fromisoformat(a)<=d+timedelta(days=6) for a in success[u['user_id']])
            add(key(u,day),u['user_id'],hit)
    if c.metric=='cpa':
        for s in tables['spend']:
            if allowed(s['channel_id']) and str(c.start)<=s['event_date']<=str(c.end):cost[key(s,s['event_date'])]+=s['amount']
    keys=set(groups)|set(cost)
    if not c.dimensions and not keys:keys={()}
    out=[]
    for k in sorted(keys):
        g=groups.get(k,{'num':set(),'denom':set()});num=len(g['num']);denom=len(g['denom'])
        if c.metric=='cpa':v=cost.get(k)/num if k in cost and num else None
        elif c.metric in ('activation_rate','retention_d7'):v=num/denom if denom else None
        else:v=num
        out.append({**dict(zip(c.dimensions,k)),'value':v})
    return out
