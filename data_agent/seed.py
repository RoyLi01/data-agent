"""Deterministic synthetic data. Never connects to a company database."""
import sqlite3, random, json
from pathlib import Path
from datetime import date, timedelta

DDL='''
CREATE TABLE users(user_id INTEGER PRIMARY KEY,registration_date TEXT NOT NULL,channel_id INTEGER NOT NULL,os TEXT NOT NULL);
CREATE TABLE activations(activation_id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,event_date TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE activity(user_id INTEGER NOT NULL,event_date TEXT NOT NULL,PRIMARY KEY(user_id,event_date));
CREATE TABLE channels(channel_id INTEGER PRIMARY KEY,channel_name TEXT UNIQUE NOT NULL,category TEXT NOT NULL);
CREATE TABLE spend(event_date TEXT NOT NULL,channel_id INTEGER NOT NULL,amount REAL NOT NULL,PRIMARY KEY(event_date,channel_id));
CREATE INDEX ix_activation ON activations(event_date,status,user_id);
CREATE INDEX ix_activity ON activity(event_date,user_id);
CREATE INDEX ix_registration ON users(registration_date,channel_id);
'''

def seed(root:Path):
    if sqlite3.sqlite_version_info < (3,39,0):
        raise RuntimeError('需要 SQLite 3.39+；请升级 Python 或使用提供的 Docker 环境')
    root.mkdir(parents=True,exist_ok=True)
    target=root/'warehouse.sqlite'
    if target.exists():
        if not (root/'watermark.json').exists(): raise RuntimeError('模拟数据水位文件缺失，请使用新运行目录重新生成')
        return
    tmp=root/'warehouse.building.sqlite'
    if tmp.exists(): raise RuntimeError('发现未完成的数据构建，请检查 warehouse.building.sqlite')
    rng=random.Random(20260922)
    db=sqlite3.connect(tmp)
    db.executescript(DDL)
    db.executemany('INSERT INTO channels VALUES (?,?,?)',[(1,'渠道A','信息流'),(2,'渠道B','信息流'),(3,'渠道C','搜索'),(4,'自然流量','自然')])
    users=[]; acts=[]; activity=set(); spend=[]; aid=0; uid=0
    start=date(2026,6,1); watermark=date(2026,9,20)
    for offset in range((watermark-start).days+1):
        day=start+timedelta(days=offset)
        for channel in range(1,5):
            spend.append((str(day),channel,0 if channel==4 else round(rng.uniform(150,650),2)))
            for _ in range(rng.randint(6,18)):
                uid+=1; os_name=rng.choice(['iOS','Android'])
                users.append((uid,str(day),channel,os_name))
                aid+=1; acts.append((aid,uid,str(day),'failed'))
                if rng.random()<(.60 if channel==2 and day.month==9 else .78):
                    ad=day+timedelta(days=rng.randint(0,8))
                    if ad<=watermark:
                        aid+=1; acts.append((aid,uid,str(ad),'success'))
                        if uid%4==0:
                            aid+=1; acts.append((aid,uid,str(ad),'success'))
                        if uid%9==0 and ad<watermark:
                            aid+=1; acts.append((aid,uid,str(ad+timedelta(days=1)),'success'))
                activity.add((uid,str(day)))
                for n in [1,3,7,14]:
                    if day+timedelta(days=n)<=watermark and rng.random()<(0.20 if channel==2 else 0.43):
                        activity.add((uid,str(day+timedelta(days=n))))
    db.executemany('INSERT INTO users VALUES (?,?,?,?)',users)
    db.executemany('INSERT INTO activations VALUES (?,?,?,?)',acts)
    db.executemany('INSERT INTO activity VALUES (?,?)',sorted(activity))
    db.executemany('INSERT INTO spend VALUES (?,?,?)',spend)
    db.commit(); db.close(); tmp.replace(target)
    (root/'watermark.json').write_text(json.dumps({'start':str(start),'complete_through':str(watermark),'snapshot':'synthetic-20260922-v1','seed':20260922,'users':len(users)},ensure_ascii=False))

if __name__=='__main__':
    from .config import Settings
    seed(Settings.env().data_dir)
