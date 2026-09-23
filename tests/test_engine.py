import asyncio,uuid,copy,time
import pytest
from data_agent.store import reuse,Conflict
from data_agent.models import Contract
from data_agent.query import Warehouse
from data_agent.skills import Budget,SkillRegistry
from data_agent.analysis import analyze
from data_agent.engine import Engine

def ask(e,q,session=None):return asyncio.run(e.ask(q,session,uuid.uuid4().hex))

def test_multiturn(engine):
    t=ask(engine,'上个月各渠道的成功激活人数');assert t['state']=='COMPLETED'
    a=ask(engine,'画柱状图',t['session_id']);assert a['reuse']['reused'] and '<svg' in a['report']['chart_svg']
    b=ask(engine,'再按操作系统拆开',t['session_id']);assert not b['reuse']['reused'] and 'os' in b['result']['columns']
    c=ask(engine,'画趋势图',t['session_id']);assert not c['reuse']['reused'] and 'date' in c['result']['columns']

def test_filter_evidence(engine):
    t=ask(engine,'上个月各渠道的注册人数')
    t=ask(engine,'只看渠道A',t['session_id'])
    assert t['reuse']['reused'] and len(t['result']['rows'])==1
    assert t['report']['evidence']['transform']=={'filter_channel':'渠道A'}

def test_resume_persists_across_engine(engine):
    t=ask(engine,'上个月各渠道新增用户数');assert t['state']=='NEEDS_CLARIFICATION'
    other=Engine(engine.settings)
    result=asyncio.run(other.resume(t['id'],'注册用户数',t['version'],'resume-1'))
    assert result['state']=='COMPLETED' and result['plan']['contract']['metric']=='registrations'
    repeated=asyncio.run(other.resume(t['id'],'注册用户数',t['version'],'resume-1'))
    assert repeated['id']==result['id'] and repeated['version']==result['version']
    with pytest.raises(Conflict):asyncio.run(other.resume(t['id'],'激活用户数',t['version'],'resume-1'))
    with pytest.raises(Conflict):asyncio.run(other.resume(t['id'],'注册用户数',t['version'],'resume-2'))

def test_idempotency_and_owner(engine):
    t=asyncio.run(engine.ask('上个月注册人数',None,'same'))
    again=asyncio.run(engine.ask('上个月注册人数',None,'same'))
    assert again['id']==t['id'] and again['version']==t['version']
    with pytest.raises(Conflict):asyncio.run(engine.ask('激活人数',None,'same'))
    with pytest.raises(KeyError):engine.store.get(t['id'],'someone-else')
    with pytest.raises(KeyError):engine.store.get_artifact(t['artifact_id'],'someone-else')

def test_pending_session_blocks_new_task(engine):
    t=ask(engine,'新增人数')
    with pytest.raises(Conflict):ask(engine,'注册人数',t['session_id'])

def test_reuse_fail_closed(engine):
    t=ask(engine,'上个月每天各渠道的成功激活人数')
    a=engine.store.get_artifact(t['artifact_id'],'local-demo');c=Contract.model_validate(a['contract']);w=Warehouse(engine.settings).watermark
    for field,value in [('is_truncated',True),('expires',time.time()-1),('snapshot','changed'),('schema_version','old'),('owner','another')]:
        bad=copy.deepcopy(a);bad[field]=value;assert not reuse(bad,c,w,'local-demo')[0]
    c.dimensions=['channel'];assert not reuse(a,c,w,'local-demo')[0]

def test_budget():
    s=SkillRegistry().load('metric_query');b=Budget(1)
    with pytest.raises(PermissionError):b.consume(s,'analyze_result')
    b.consume(s,'query_metric')
    with pytest.raises(ValueError):b.consume(s,'query_metric')

def test_analysis_rejects_preview(engine):
    t=ask(engine,'注册人数');a=engine.store.get_artifact(t['artifact_id'],'local-demo');a['is_truncated']=True
    with pytest.raises(ValueError,match='截断'):analyze(a,'bar')

def test_real_mcp_transport(settings):
    settings.transport='stdio';e=Engine(settings)
    t=ask(e,'上个月信息流渠道激活成本')
    assert t['state']=='COMPLETED',t.get('error')
    assert len(t['result']['rows'])==2

def test_live_missing_credentials_fails(settings,monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY',raising=False);settings.mode='live'
    t=ask(Engine(settings),'注册人数')
    assert t['state']=='FAILED' and '尚未配置' in t['error']['message']
