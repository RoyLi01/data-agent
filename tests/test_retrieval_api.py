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
