"""Hybrid ranking. Offline hashed vectors are explicitly NOT semantic embeddings."""
from collections import Counter
import hashlib,math,re,os
import numpy as np
import httpx
from .catalog import chunks,required_ids,SCHEMA_VERSION

def tokens(text):
    text=text.lower()
    parts=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text)
    result=[]
    for p in parts:
        if re.fullmatch(r'[\u4e00-\u9fff]+',p):
            result.extend(p); result.extend(p[i:i+2] for i in range(len(p)-1))
        else: result.append(p)
    return result

class Retriever:
    def __init__(self):
        self.docs=chunks(); self.by_id={d['id']:d for d in self.docs}
        self.tf=[Counter(tokens(d['text'])) for d in self.docs]
        self.df=Counter(t for c in self.tf for t in c)
        self.avg=sum(sum(c.values()) for c in self.tf)/len(self.tf)
        self.matrix=None; self.milvus=None
        self.semantic=bool(os.getenv('EMBEDDING_MODEL'))

    def embed(self,texts):
        if self.semantic:
            key=os.environ.get('EMBEDDING_API_KEY','')
            if not key: raise ValueError('已选择语义向量模型，但尚未配置 EMBEDDING_API_KEY')
            with httpx.Client(timeout=60,trust_env=False) as client:
                r=client.post(os.environ['EMBEDDING_BASE_URL'].rstrip('/')+'/embeddings',headers={'Authorization':'Bearer '+key},json={'model':os.environ['EMBEDDING_MODEL'],'input':texts})
                r.raise_for_status(); data=sorted(r.json()['data'],key=lambda x:x['index'])
                values=np.array([x['embedding'] for x in data],dtype=float)
                if len(values)!=len(texts): raise ValueError('Embedding 返回数量不匹配')
        else:
            values=np.zeros((len(texts),512))
            for i,text in enumerate(texts):
                for tok in tokens(text):
                    h=int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8],'little')
                    values[i,h%512]+=1 if h%2 else -1
        if not np.isfinite(values).all(): raise ValueError('Embedding 包含非法数值')
        return values/np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-12)

    def _init_vectors(self):
        if self.matrix is not None:return
        self.matrix=self.embed([d['text'] for d in self.docs])
        if os.getenv('MILVUS_URI'):
            from pymilvus import MilvusClient
            self.milvus=MilvusClient(uri=os.environ['MILVUS_URI'],token=os.getenv('MILVUS_TOKEN',''))
            identity=hashlib.sha256((SCHEMA_VERSION+os.getenv('EMBEDDING_MODEL','offline_hash')+str(self.matrix.shape[1])).encode()).hexdigest()[:16]
            self.collection='metadata_'+identity
            if not self.milvus.has_collection(self.collection):
                self.milvus.create_collection(self.collection,dimension=self.matrix.shape[1],metric_type='COSINE',consistency_level='Strong')
            self.milvus.upsert(self.collection,[{'id':i,'vector':v.tolist(),'chunk_id':d['id']} for i,(d,v) in enumerate(zip(self.docs,self.matrix))])

    def search(self,question,contract=None,k=8,expand=True):
        q=Counter(tokens(question)); n=len(self.docs); scores=[]
        for c in self.tf:
            length=sum(c.values()); score=0
            for token in q:
                f=c[token]; idf=math.log(1+(n-self.df[token]+.5)/(self.df[token]+.5))
                score+=idf*f*2.5/(f+1.5*(.25+.75*length/self.avg)) if f else 0
            scores.append(score)
        bm=sorted(range(n),key=lambda i:scores[i],reverse=True)[:min(24,n)]
        self._init_vectors(); query=self.embed([question])[0]
        if self.milvus:
            hits=self.milvus.search(self.collection,data=[query.tolist()],limit=min(24,n))[0]
            dense=[int(x['id']) for x in hits]
        else: dense=np.argsort(-(self.matrix@query)).tolist()[:min(24,n)]
        fused=Counter()
        for ranking in [bm,dense]:
            for rank,i in enumerate(ranking):fused[i]+=1/(60+rank+1)
        candidates=sorted(fused,key=fused.get,reverse=True)[:20]
        rerank_mode='offline_token_overlap'
        if os.getenv('RERANK_URL'):
            with httpx.Client(timeout=60,trust_env=False) as client:
                r=client.post(os.environ['RERANK_URL'],headers={'Authorization':'Bearer '+os.getenv('RERANK_API_KEY','')},json={'model':os.getenv('RERANK_MODEL',''),'query':question,'documents':[self.docs[i]['text'] for i in candidates],'top_n':len(candidates)})
                r.raise_for_status(); ranked=r.json()['results']
                candidates=[candidates[x['index']] for x in ranked]; rerank_mode='remote'
        else:
            candidates.sort(key=lambda i:(sum(min(q[t],self.tf[i][t]) for t in q)/max(1,len(q)),fused[i]),reverse=True)
        selected=[dict(self.docs[i],source='retrieved',rrf_score=round(fused[i],6)) for i in candidates[:k]]
        present={d['id'] for d in selected}; missing=[]
        if contract and expand:
            for key in sorted(required_ids(contract)-present):
                if key not in self.by_id: raise ValueError('注册表依赖缺失：'+key)
                selected.append(dict(self.by_id[key],source='dependency')); missing.append(key)
        return {'chunks':selected,'added_dependencies':missing,'embedding_mode':'remote_semantic' if self.semantic else 'offline_lexical_hash',
                'rerank_mode':rerank_mode,'vector_store':'milvus' if self.milvus else 'in_memory','schema_version':SCHEMA_VERSION}
