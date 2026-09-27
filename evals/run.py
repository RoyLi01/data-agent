"""Offline engineering benchmark. Never reports fixture accuracy as LLM accuracy."""
import asyncio,json,statistics,tempfile,time,uuid,sys,os
os.environ["RETRIEVAL_BACKEND"]="fixture"
os.environ["RERANK_BACKEND"]="fixture"
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from data_agent.config import Settings
from data_agent.engine import Engine
from data_agent.models import Contract
from data_agent.catalog import required_ids
from data_agent.retrieval import Retriever
from evals.reference import calculate

CASES=[
 ('上个月各渠道注册人数','registrations',['channel'],None),
 ('上月总体注册人数','registrations',[],None),
 ('上个月每天各渠道注册人数','registrations',['date','channel'],None),
 ('上个月各渠道注册人数按操作系统拆分','registrations',['channel','os'],None),
 ('上个月信息流渠道注册人数','registrations',['channel'],'信息流'),
 ('上个月搜索渠道注册人数','registrations',['channel'],'搜索'),
 ('上个月各渠道成功激活人数','activations',['channel'],None),
 ('上月总体成功激活人数','activations',[],None),
 ('上个月每天各渠道成功激活人数','activations',['date','channel'],None),
 ('上个月各渠道成功激活人数按操作系统拆分','activations',['channel','os'],None),
 ('上个月信息流渠道成功激活人数','activations',['channel'],'信息流'),
 ('上个月搜索渠道成功激活人数','activations',['channel'],'搜索'),
 ('上个月各渠道转化率','activation_rate',['channel'],None),
 ('上月总体激活率','activation_rate',[],None),
 ('上个月每天各渠道激活率','activation_rate',['date','channel'],None),
 ('上个月各渠道激活率按操作系统拆分','activation_rate',['channel','os'],None),
 ('上个月信息流渠道激活率','activation_rate',['channel'],'信息流'),
 ('上个月搜索渠道激活率','activation_rate',['channel'],'搜索'),
 ('上个月各渠道D7留存','retention_d7',['channel'],None),
 ('上月总体七日留存','retention_d7',[],None),
 ('上个月每天各渠道留存率','retention_d7',['date','channel'],None),
 ('上个月各渠道留存按操作系统拆分','retention_d7',['channel','os'],None),
 ('上个月信息流渠道D7留存','retention_d7',['channel'],'信息流'),
 ('上个月搜索渠道D7留存','retention_d7',['channel'],'搜索'),
 ('上个月各渠道激活成本','cpa',['channel'],None),
 ('上月总体CPA','cpa',[],None),
 ('上个月每天各渠道激活成本','cpa',['date','channel'],None),
 ('上个月信息流渠道激活成本','cpa',['channel'],'信息流'),
 ('上个月搜索渠道激活成本','cpa',['channel'],'搜索'),
 ('上个月自然渠道激活成本','cpa',['channel'],'自然'),
]

def same(a,b,dims):
    def keyed(rows):return {tuple(r[d] for d in dims):r['value'] for r in rows}
    aa,bb=keyed(a),keyed(b)
    return aa.keys()==bb.keys() and all((aa[k] is None and bb[k] is None) or (aa[k] is not None and bb[k] is not None and abs(aa[k]-bb[k])<1e-7) for k in aa)

async def evaluate(root):
    e=Engine(Settings(root,transport='local'));details=[];latencies=[];r=Retriever();coverage={'without_expansion':0,'with_expansion':0}
    for q,m,d,category in CASES:
        c=Contract(metric=m,start='2026-08-01',end='2026-08-31',dimensions=d,category=category)
        start=time.perf_counter();t=await e.ask(q,None,uuid.uuid4().hex);latencies.append((time.perf_counter()-start)*1000)
        actual=e.store.get_artifact(t['artifact_id'],'local-demo')['rows'] if t['state']=='COMPLETED' else []
        ok=t['state']=='COMPLETED' and t['plan']['contract']==c.model_dump(mode='json') and same(actual,calculate(root,c),d)
        per={}
        for label,expand in [('without_expansion',False),('with_expansion',True)]:
            found={x['id'] for x in r.search(q,c,expand=expand)['chunks']}
            full=required_ids(c)<=found;coverage[label]+=int(full);per[label]=full
        details.append({'question':q,'correct':ok,'state':t['state'],'dependency_complete':per})
    turns=[]
    for base,follow,expected in [
      ('上个月各渠道注册人数','画柱状图',True),('上个月各渠道注册人数','只看渠道A',True),
      ('上个月各渠道注册人数','画趋势图',False),('上个月各渠道注册人数','再按操作系统拆开',False),
      ('上个月每天各渠道成功激活人数','不要按天，总体成功激活人数',False),
      ('上个月各渠道激活率','画柱状图',True),('上个月各渠道留存率','再按操作系统拆开',False),
      ('上个月各渠道注册人数','2026-07-01 至 2026-07-31 注册人数',False)]:
        a=await e.ask(base,None,uuid.uuid4().hex);b=await e.ask(follow,a['session_id'],uuid.uuid4().hex)
        turns.append({'base':base,'followup':follow,'correct':b['state']=='COMPLETED' and b['reuse']['reused']==expected})
    clarify=[]
    for q,answer in [('上个月新增人数','注册人数'),('新增用户按渠道','成功激活人数')]:
        a=await e.ask(q,None,uuid.uuid4().hex)
        b=await e.resume(a['id'],answer,a['version'],uuid.uuid4().hex) if a['state']=='NEEDS_CLARIFICATION' else a
        clarify.append({'question':q,'correct':a['state']=='NEEDS_CLARIFICATION' and b['state']=='COMPLETED'})
    e.memory.close()
    n=len(details)
    return {'evaluation_type':'offline_engineering_fixture','llm_accuracy':None,
        'warning':'开发回归集，不是独立真实模型测试；不支持据此声称 LLM 准确率或线上收益。依赖补全覆盖提升是注册表规则覆盖效果。',
        'query_cases':n,'query_correct':sum(x['correct'] for x in details),'multi_turn_cases':len(turns),'multi_turn_correct':sum(x['correct'] for x in turns),
        'clarification_cases':len(clarify),'clarification_correct':sum(x['correct'] for x in clarify),
        'dependency_complete_count':coverage,'end_to_end_ms':{'p50':round(statistics.median(latencies),2),'p95':round(sorted(latencies)[int(.95*(len(latencies)-1))],2),'transport':'local'},
        'details':details,'multi_turn':turns,'clarification':clarify}

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='data-agent-eval-') as tmp:result=asyncio.run(evaluate(Path(tmp)))
    out=ROOT/'evals/latest_offline.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ['details','multi_turn','clarification']},ensure_ascii=False,indent=2))
    print('Saved:',out)
    if result['query_correct']!=result['query_cases'] or result['multi_turn_correct']!=result['multi_turn_cases'] or result['clarification_correct']!=result['clarification_cases']:sys.exit(1)
