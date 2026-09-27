"""Small real-model retrieval smoke set, separate from fixture planner evaluation."""
import sys,json,time,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from data_agent.retrieval import Retriever,EMBED_MODEL,RERANK_MODEL
from data_agent.catalog import SCHEMA_VERSION
CASES=[
 ('用户完成注册后一周还有多少人回来使用？','metric:retention_d7'),
 ('每带来一个成功激活的客户，推广平均需要花多少钱？','metric:cpa'),
 ('注册后的头七天内开始使用产品的人占注册人数的比例','metric:activation_rate'),
 ('按开户日期计算新增注册账户总数','metric:registrations'),
 ('排除失败和重复事件后实际激活的人数','metric:activations'),
 ('只看信息流而不是搜索投放，应使用哪个分类字段？','field:channels.category'),
]
def main():
    r=Retriever('local','local');details=[]
    for question,expected in CASES:
        start=time.perf_counter();out=r.search(question,k=5)
        ids=out['stages']['reranked_candidates'][:5]
        details.append({'question':question,'expected':expected,'top5':ids,'hit_at_5':expected in ids,
                        'rank':ids.index(expected)+1 if expected in ids else None,'elapsed_ms':round((time.perf_counter()-start)*1000,2)})
    report={'evaluation_type':'local_neural_retrieval_smoke','llm_accuracy':None,'embedding_model':EMBED_MODEL,
            'rerank_model':RERANK_MODEL,'schema_version':SCHEMA_VERSION,'cases':len(details),
            'hit_at_5_count':sum(x['hit_at_5'] for x in details),'details':details,
            'note':'真实本地 Embedding + CrossEncoder 的小样本开发验证；未调用 LLM，不是独立盲测或生产准确率。首次延迟包括模型加载。'}
    path=ROOT/'evals/latest_local_retrieval.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
