import itertools,sqlite3
import pytest
from data_agent.models import Contract
from data_agent.query import Warehouse,compile_query,validate_sql,QueryError
from evals.reference import calculate

@pytest.mark.parametrize('metric,dims',[(m,d) for m in ['registrations','activations','activation_rate','retention_d7','cpa'] for d in [[],['channel'],['date','channel']] ] +[(m,['channel','os']) for m in ['registrations','activations','retention_d7']])
def test_independent_reference(settings,metric,dims):
    c=Contract(metric=metric,start='2026-08-01',end='2026-08-31',dimensions=dims,category='信息流')
    actual=Warehouse(settings).query(c.model_dump(mode='json'))['rows'];expected=calculate(settings.data_dir,c)
    assert len(actual)==len(expected)
    for a,b in zip(actual,expected):
        assert all(a[d]==b[d] for d in dims)
        assert a['value']==pytest.approx(b['value']) if b['value'] is not None else a['value'] is None

def test_maturity(settings):
    c=Contract(metric='retention_d7',start='2026-09-01',end='2026-09-20')
    r=Warehouse(settings).query(c.model_dump(mode='json'))
    assert r['effective_end']=='2026-09-13' and r['warnings']
    expected=calculate(settings.data_dir,c)
    assert [x['value'] for x in r['rows']]==pytest.approx([x['value'] for x in expected])

def test_incomplete_data(settings):
    with pytest.raises(QueryError,match='数据完整范围'):Warehouse(settings).query({'metric':'registrations','start':'2026-09-01','end':'2026-09-22'})

def test_cpa_os_rejected():
    with pytest.raises(ValueError,match='操作系统'):Contract(metric='cpa',start='2026-08-01',end='2026-08-31',dimensions=['os'])

@pytest.mark.parametrize('sql',['DELETE FROM users','SELECT user_id FROM users; DROP TABLE users','SELECT * FROM users','SELECT password FROM users','SELECT user_id FROM sqlite_master','SELECT load_extension(\'x\') FROM users','SELECT user_id FROM main.users'])
def test_bad_sql_rejected(sql):
    with pytest.raises(QueryError):validate_sql(sql)

def test_semantic_mutations_rejected(settings):
    c=Contract(metric='activations',start='2026-08-01',end='2026-08-31')
    sql,_=compile_query(c,Warehouse(settings).watermark)
    for bad in [sql.replace(" AND a.status='success'",''),sql.replace('COUNT(DISTINCT a.user_id)','COUNT(a.user_id)'),sql.replace('2026-08-01','2026-08-02')]:
        with pytest.raises(QueryError,match='模板不一致'):validate_sql(bad,sql)

def test_timeout_and_truncation(settings):
    settings.max_rows=3
    r=Warehouse(settings).query({'metric':'registrations','start':'2026-08-01','end':'2026-08-31','dimensions':['date','channel']})
    assert r['is_truncated'] and len(r['rows'])==3
    settings.query_timeout=-1
    with pytest.raises(QueryError):Warehouse(settings).query({'metric':'activations','start':'2026-08-01','end':'2026-08-31'})
