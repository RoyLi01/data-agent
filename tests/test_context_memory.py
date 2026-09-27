import asyncio,json,time,uuid,threading
import pytest
from data_agent.context import ContextAggregator
from data_agent.memory import Memory
from data_agent.engine import Engine
from data_agent.store import Conflict

OWNER='local-demo'
def ask(e,q,session=None,memories=None):
    t=asyncio.run(e.ask(q,session,uuid.uuid4().hex,memory_ids=memories))
    assert t['state'] in ('COMPLETED','NEEDS_CLARIFICATION'),t.get('error')
    return t

def test_return_to_first_result_and_refresh(engine):
    first=ask(engine,'2026-08-01 至 2026-08-31 各渠道注册人数')
    other=ask(engine,'2026-07-01 至 2026-07-31 各渠道激活人数',first['session_id'])
    back=ask(engine,'回到第一个结果画柱状图',first['session_id'])
    assert back['artifact_id']==first['artifact_id']
    assert back['routing']['effective']=='analyze'
    assert back['plan']['contract']['metric']=='registrations'
    assert '2026-08-01' in back['context']['aggregate']['request']
    following=ask(engine,'只看渠道A',first['session_id'])
    assert following['plan']['contract']['metric']=='registrations'
    refreshed=ask(engine,'刷新数据',first['session_id'])
    assert refreshed['routing']['effective']=='query'
    assert refreshed['artifact_id']!=following['artifact_id']

def test_long_memory_explicit_and_cross_session(engine):
    first=ask(engine,'2026-07-01 至 2026-07-31 各渠道注册人数')
    assert engine.memory.list(OWNER)==[]
    saved=engine.memory.save(OWNER,'result',first['artifact_id'],'七月注册')
    no_ref=ask(engine,'画柱状图')
    assert no_ref['state']=='NEEDS_CLARIFICATION'
    t=ask(engine,'画柱状图',memories=[saved['id']])
    assert t['session_id']!=first['session_id'] and t['artifact_id']==first['artifact_id']
    assert t['routing']['effective']=='analyze'
    follow=ask(engine,'只看渠道A',t['session_id'])
    assert follow['reuse']['reused'] and follow['plan']['contract']['start']=='2026-07-01'
    with engine.store.connect() as db:
        a=engine.store.get_artifact(first['artifact_id'],OWNER);a['expires']=time.time()-1
        db.execute('UPDATE artifacts SET payload=? WHERE id=?',(json.dumps(a),a['id']))
    fresh=ask(engine,'画柱状图',memories=[saved['id']])
    assert fresh['routing']['effective']=='query'
    assert fresh['plan']['contract']['start']=='2026-07-01'
    engine.memory.delete(OWNER,saved['id'])
    with pytest.raises(KeyError):engine.memory.selected(OWNER,[saved['id']])

def test_long_memory_owner_and_schema(engine):
    m=engine.memory.save(OWNER,'field','users.user_id',note='去重标识')
    assert engine.memory.list(OWNER,'去重')[0]['id']==m['id']
    assert engine.memory.list('other')==[]
    with pytest.raises(KeyError):engine.memory.selected('other',[m['id']])
    with pytest.raises(KeyError):engine.memory.delete('other',m['id'])
    with pytest.raises(ValueError):engine.memory.save(OWNER,'field','users.password')
    t=ask(engine,'注册人数',memories=[m['id']])
    assert t['context']['selected_memory_ids']==[m['id']]
    with engine.store.connect() as db:
        payload={**m['payload'],'schema_version':'old'}
        db.execute('UPDATE memories SET payload=? WHERE id=?',(json.dumps(payload),m['id']))
    assert not engine.memory.selected(OWNER,[m['id']])[0]['valid_schema']

def test_async_summary_sliding_window_and_restart(engine):
    first=ask(engine,'各渠道注册人数');sid=first['session_id']
    for _ in range(8):ask(engine,'画柱状图',sid)
    assert engine.memory.flush()
    snapshot=engine.memory.snapshot(sid,OWNER)
    assert len(snapshot['recent_turns'])==6
    assert snapshot['summary'] and snapshot['summary_cursor']>0
    assert not snapshot['summary_pending']
    other=Memory(engine.store)
    assert other.snapshot(sid,OWNER)==snapshot
    with engine.store.connect() as db:
        db.execute("UPDATE summary_jobs SET state='running' WHERE cursor=?",(snapshot['summary_cursor'],))
    other.recover();assert other.flush()
    assert other.snapshot(sid,OWNER)['summary_cursor']==snapshot['summary_cursor']
    other.close()

def test_summary_does_not_block_foreground_and_cursor_monotonic(engine):
    entered=threading.Event();release=threading.Event()
    class SlowModel:
        def call(self,*args):
            entered.set();assert release.wait(5)
            return {'summary':'旧摘要'},{}
    engine.memory.model=SlowModel();engine.memory.window=1
    first=ask(engine,'各渠道注册人数')
    second=ask(engine,'画柱状图',first['session_id'])
    assert entered.wait(2) and second['state']=='COMPLETED'
    with engine.store.connect() as db:
        db.execute('INSERT INTO context_summaries VALUES (?,?,?,?)',(first['session_id'],OWNER,999,'更新摘要'))
    release.set();assert engine.memory.flush()
    assert engine.memory.snapshot(first['session_id'],OWNER)['summary']=='更新摘要'

def test_memory_selection_is_part_of_idempotency(engine):
    m=engine.memory.save(OWNER,'table','users')
    asyncio.run(engine.ask('注册人数',None,'same-memory',memory_ids=[m['id']]))
    with pytest.raises(Conflict):asyncio.run(engine.ask('注册人数',None,'same-memory'))

def test_live_context_adapter_validates_artifact_references(engine):
    engine.planner.mode='live'
    class Fake:
        def call(self,messages,name,schema):
            assert name=='aggregate_request' and 'recent_turns' in messages[1]['content']
            return {'request':'2026年8月注册用户画柱状图','intent':'analyze','artifact_id':'not-accessible'},{}
    engine.planner.model=Fake()
    with pytest.raises(ValueError,match='不可访问'):
        engine.aggregator.aggregate('画图',{'recent_turns':[]},[],[])


def test_live_aggregate_plan_analysis_integration(settings,monkeypatch):
    from data_agent.planner import ToolModel
    settings.mode='live';calls=[]
    contract={'metric':'registrations','start':'2026-08-01','end':'2026-08-31','dimensions':['channel']}
    def fake_call(self,messages,name,schema):
        calls.append(name)
        if name=='aggregate_request':return {'request':'上个月各渠道注册人数','intent':'query'},{}
        if name=='submit_plan':
            assert 'schema_graph' in messages[1]['content']
            return {'action':'query','contract':contract,'analysis':'table'},{}
        if name=='select_analysis':return {'kind':'table'},{}
        raise AssertionError(name)
    monkeypatch.setattr(ToolModel,'call',fake_call)
    e=Engine(settings)
    try:
        t=ask(e,'上个月各渠道注册人数')
        assert t['context']['model']['mode']=='live' and t['routing']['effective']=='query'
        assert calls==['aggregate_request','submit_plan','select_analysis']
    finally:e.memory.close()
