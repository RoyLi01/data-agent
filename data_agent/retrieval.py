"""BM25 + independent dense recall -> RRF -> cross-encoder -> dependency/graph closure."""
from collections import Counter
from functools import lru_cache
import hashlib,json,math,re,os
import numpy as np
import httpx
from .catalog import required_ids,SCHEMA_VERSION
from .schema import index_documents,schema_graph
from .config import ROOT

EMBED_MODEL='BAAI/bge-small-zh-v1.5'
RERANK_MODEL='cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'

def tokens(text):
    result=[]
    for p in re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text.lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+',p):result.extend(p);result.extend(p[i:i+2] for i in range(len(p)-1))
        else:result.append(p)
    return result

@lru_cache(maxsize=4)
def local_model(name,rerank=False):
    try:
        from sentence_transformers import SentenceTransformer,CrossEncoder
    except ImportError as exc:
        raise ValueError("本地语义检索需要安装 requirements-neural.txt 中的依赖") from exc
    cache=str(ROOT/'runtime/models')
    if rerank:return CrossEncoder(name,device='cpu',cache_folder=cache,trust_remote_code=False,max_length=512)
    return SentenceTransformer(name,device='cpu',cache_folder=cache,trust_remote_code=False)

class Retriever:
    def __init__(self,backend=None,rerank_backend=None):
        self.backend=backend or os.getenv('RETRIEVAL_BACKEND','remote' if os.getenv('EMBEDDING_MODEL') else 'fixture')
        self.rerank_backend=rerank_backend or os.getenv('RERANK_BACKEND','remote' if os.getenv('RERANK_URL') else ('local' if self.backend=='local' else 'fixture'))
        if self.backend not in ('fixture','local','remote') or self.rerank_backend not in ('fixture','local','remote'):raise ValueError('未知检索后端')
        self.semantic=self.backend!='fixture';self.docs=index_documents();self.by_id={d['id']:d for d in self.docs}
        self.tf=[Counter(tokens(d['keyword_text'])) for d in self.docs]
        self.df=Counter(t for c in self.tf for t in c);self.avg=sum(sum(c.values()) for c in self.tf)/len(self.tf)
        self.matrix=None;self.milvus=None

    def embed(self,texts,query=False):
        if self.backend=='local':
            name=os.getenv('LOCAL_EMBEDDING_MODEL',EMBED_MODEL)
            if query and name==EMBED_MODEL:texts=['为这个句子生成表示以用于检索相关文章：'+t for t in texts]
            values=np.asarray(local_model(name).encode(texts,normalize_embeddings=True,show_progress_bar=False,batch_size=16),dtype=float)
        elif self.backend=='remote':
            key=os.getenv('EMBEDDING_API_KEY','')
            if not key:raise ValueError('请配置 EMBEDDING_API_KEY')
            with httpx.Client(timeout=60,trust_env=False) as client:
                r=client.post(os.environ['EMBEDDING_BASE_URL'].rstrip('/')+'/embeddings',headers={'Authorization':'Bearer '+key},json={'model':os.environ['EMBEDDING_MODEL'],'input':texts})
                r.raise_for_status();data=sorted(r.json()['data'],key=lambda x:x['index']);values=np.array([x['embedding'] for x in data],dtype=float)
        else:
            values=np.zeros((len(texts),512))
            for i,text in enumerate(texts):
                for tok in tokens(text):
                    h=int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8],'little');values[i,h%512]+=1 if h%2 else -1
        if values.ndim!=2 or len(values)!=len(texts) or not np.isfinite(values).all():raise ValueError('Embedding 返回格式或数值异常')
        return values/np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-12)

    def _init_vectors(self):
        if self.matrix is not None:return
        matrix=self.embed([d['semantic_text'] for d in self.docs])
        if os.getenv('MILVUS_URI'):
            from pymilvus import MilvusClient
            client=MilvusClient(uri=os.environ['MILVUS_URI'],token=os.getenv('MILVUS_TOKEN',''))
            ident=hashlib.sha256(json.dumps([self.backend,os.getenv('LOCAL_EMBEDDING_MODEL',EMBED_MODEL),os.getenv('EMBEDDING_MODEL'),self.docs,matrix.shape[1]],sort_keys=True).encode()).hexdigest()[:16]
            collection='metadata_'+ident
            if not client.has_collection(collection):client.create_collection(collection,dimension=matrix.shape[1],metric_type='COSINE',consistency_level='Strong')
            client.upsert(collection,[{'id':i,'vector':v.tolist(),'chunk_id':d['id']} for i,(d,v) in enumerate(zip(self.docs,matrix))])
            self.milvus=client;self.collection=collection
        self.matrix=matrix

    def rerank(self,question,indices):
        texts=[self.docs[i]['rerank_text'] for i in indices]
        if self.rerank_backend=='local':
            scores=np.asarray(local_model(os.getenv('LOCAL_RERANK_MODEL',RERANK_MODEL),True).predict([(question,t) for t in texts],show_progress_bar=False,batch_size=8)).reshape(-1)
        elif self.rerank_backend=='remote':
            with httpx.Client(timeout=60,trust_env=False) as client:
                r=client.post(os.environ['RERANK_URL'],headers={'Authorization':'Bearer '+os.getenv('RERANK_API_KEY','')},json={'model':os.getenv('RERANK_MODEL',''),'query':question,'documents':texts,'top_n':len(texts)})
                r.raise_for_status();entries=r.json()['results']
            indexes=[e['index'] for e in entries]
            if sorted(indexes)!=list(range(len(indices))):raise ValueError('重排响应必须无重复地覆盖全部候选')
            scores=np.zeros(len(indices))
            for rank,e in enumerate(entries):scores[e['index']]=e.get('relevance_score',len(entries)-rank)
        else:
            q=set(tokens(question));scores=np.array([len(q&set(tokens(t)))/max(1,len(q)) for t in texts])
        if len(scores)!=len(indices) or not np.isfinite(scores).all():raise ValueError('重排分数格式错误')
        return sorted(zip(indices,scores.tolist()),key=lambda p:p[1],reverse=True)

    def search(self,question,contract=None,k=8,expand=True,allowed_tables=None,original_question=None):
        q=Counter(tokens(question));n=len(self.docs);bm_scores=[]
        allowed=set(allowed_tables) if allowed_tables is not None else None
        def permitted(d):
            if allowed is None:return True
            if d['kind']=='field':return d['table'] in allowed
            if d['kind']=='table':return d['id'][6:] in allowed
            # Metric/relation metadata cannot leak inaccessible tables.
            if d['kind']=='relation':return all(part.split('.')[0] in allowed for part in d['id'][9:].split('='))
            from .catalog import METRICS
            return all(x.split('.')[0] in allowed for x in METRICS[d['id'][7:]]['deps'])
        candidates_allowed={i for i,d in enumerate(self.docs) if permitted(d)}
        for c in self.tf:
            size=sum(c.values());s=0
            for t in q:
                f=c[t];idf=math.log(1+(n-self.df[t]+.5)/(self.df[t]+.5))
                if f:s+=idf*f*2.5/(f+1.5*(.25+.75*size/self.avg))
            bm_scores.append(s)
        bm=sorted(candidates_allowed,key=lambda i:bm_scores[i],reverse=True)[:24]
        self._init_vectors();vector=self.embed([question],query=True)[0]
        if self.milvus:
            hits=self.milvus.search(self.collection,data=[vector.tolist()],limit=n)[0]
            dense=[int(x['id']) for x in hits if int(x['id']) in candidates_allowed][:24]
        else:dense=[i for i in np.argsort(-(self.matrix@vector)).tolist() if i in candidates_allowed][:24]
        fused=Counter()
        for ranking in (bm,dense):
            for rank,i in enumerate(ranking):fused[i]+=1/(61+rank)
        candidates=sorted(fused,key=lambda i:(-fused[i],i))[:24]
        ranked=self.rerank(original_question or question,candidates) if candidates else []
        selected=[dict(self.docs[i],source='retrieved',rrf_score=round(fused[i],6),rerank_score=round(score,6)) for i,score in ranked[:k]]
        present={d['id'] for d in selected};added=[]
        if contract and expand:
            for key in sorted(required_ids(contract)-present):
                if key not in self.by_id:raise ValueError('指标依赖不存在：'+key)
                if not permitted(self.by_id[key]):raise PermissionError('指标依赖超出允许 Schema 范围')
                selected.append(dict(self.by_id[key],source='dependency'));added.append(key)
        graph=schema_graph(selected,contract if expand else None)
        if allowed is not None and any(x['table'] not in allowed for x in graph['nodes']):raise PermissionError('Schema 关联路径经过未授权表')
        for field in graph['required_join_fields']:
            key='field:'+field
            if key not in {d['id'] for d in selected}:
                selected.append(dict(self.by_id[key],source='graph_join'));added.append(key)
        return {'chunks':selected,'added_dependencies':added,'schema_graph':graph,
                'stages':{'keyword_candidates':[self.docs[i]['id'] for i in bm],'semantic_candidates':[self.docs[i]['id'] for i in dense],
                          'fused_candidates':[self.docs[i]['id'] for i in candidates],'reranked_candidates':[self.docs[i]['id'] for i,s in ranked]},
                'index_views':['keyword_text','semantic_text','rerank_text'],
                'embedding_mode':{'fixture':'offline_lexical_hash','local':'local_semantic','remote':'remote_semantic'}[self.backend],
                'rerank_mode':{'fixture':'offline_token_overlap','local':'local_cross_encoder','remote':'remote'}[self.rerank_backend],
                'vector_store':'milvus' if self.milvus else 'in_memory','schema_version':SCHEMA_VERSION}
