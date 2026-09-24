import uuid
from fastapi.testclient import TestClient
from data_agent.retrieval import Retriever
from data_agent.models import Contract
from data_agent.catalog import required_ids
from data_agent.api import app
from data_agent.config import Settings

def test_dependency_closure():
    c=Contract(metric='retention_d7',start='2026-08-01',end='2026-08-31',category='信息流')
    r=Retriever().search('上月留存',c,k=1)
    assert required_ids(c)<={x['id'] for x in r['chunks']}
    assert r['added_dependencies'] and r['embedding_mode']=='offline_lexical_hash'

def test_api(tmp_path,monkeypatch):
    monkeypatch.setattr(Settings,'env',classmethod(lambda cls:Settings(tmp_path,transport='local')))
    monkeypatch.setenv('AGENT_ACCESS_TOKEN','unit-test-token')
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/catalog').status_code==401
        auth={'Authorization':'Bearer unit-test-token'}
        response=client.post('/api/ask',json={'question':'上个月各渠道注册人数','request_id':uuid.uuid4().hex},headers=auth)
        t=response.json();assert response.status_code==200 and t['state']=='COMPLETED'
        assert client.get('/api/tasks/'+t['id']+'/report',headers=auth).status_code==200
        assert client.post('/api/ask',json={'question':'注册人数','request_id':'x'},headers={**auth,'Origin':'https://example.com'}).status_code==403

def test_three_views_and_schema_bridge():
    from data_agent.schema import index_documents,schema_graph
    docs={d['id']:d for d in index_documents()}
    field=docs['field:activations.status']
    assert len({field[k] for k in ['keyword_text','semantic_text','rerank_text']})==3
    assert 'success' in field['rerank_text']
    graph=schema_graph([docs['field:activations.user_id'],docs['field:spend.amount']])
    assert {'users','channels'}<={n['table'] for n in graph['nodes']}
    assert {'users.channel_id','channels.channel_id','spend.channel_id'}<=set(graph['required_join_fields'])
    assert any(len(path)==4 for path in graph['paths'])

def test_independent_recall_rrf_rerank_and_scope():
    r=Retriever().search('注册时间',allowed_tables=['users'])
    stages=r['stages']
    assert stages['keyword_candidates'] and stages['semantic_candidates']
    assert set(stages['fused_candidates'])<=set(stages['keyword_candidates']+stages['semantic_candidates'])
    assert set(stages['reranked_candidates'])==set(stages['fused_candidates'])
    assert all(n['table']=='users' for n in r['schema_graph']['nodes'])
    c=Contract(metric='cpa',start='2026-08-01',end='2026-08-31')
    import pytest
    with pytest.raises(PermissionError):Retriever().search('成本',c,allowed_tables=['users'])

def test_memories_api_and_cross_session(tmp_path,monkeypatch):
    monkeypatch.setattr(Settings,'env',classmethod(lambda cls:Settings(tmp_path,transport='local')))
    monkeypatch.delenv('AGENT_ACCESS_TOKEN',raising=False)
    with TestClient(app) as client:
        first=client.post('/api/ask',json={'question':'各渠道注册人数','request_id':'first'}).json()
        saved=client.post('/api/memories',json={'kind':'result','source_id':first['artifact_id'],'title':'月度注册'}).json()
        assert client.get('/api/memories?q=月度').json()['items'][0]['id']==saved['id']
        task=client.post('/api/ask',json={'question':'画柱状图','request_id':'new','memory_ids':[saved['id']]}).json()
        assert task['state']=='COMPLETED' and task['routing']['effective']=='analyze'
        assert task['session_id']!=first['session_id']
        assert client.get('/api/sessions/'+task['session_id']+'/context').json()['recent_turns']
        assert client.post('/api/memories',json={'kind':'field','source_id':'not_exists.id'}).status_code==422
        assert client.delete('/api/memories/'+saved['id']).status_code==200
        assert client.get('/api/memories').json()['items']==[]
