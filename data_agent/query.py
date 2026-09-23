from datetime import date,timedelta
import sqlite3,time,json
from pathlib import Path
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from .models import Contract
from .catalog import TABLES,SCHEMA_VERSION

class QueryError(ValueError):
    def __init__(self,code,message): self.code=code; super().__init__(message)

def literal(x): return exp.Literal.string(str(x)).sql(dialect='sqlite')

def compile_query(c:Contract,watermark:dict):
    latest=date.fromisoformat(watermark['complete_through'])
    earliest=date.fromisoformat(watermark['start'])
    if c.start<earliest or c.end>latest:
        raise QueryError('DATA_INCOMPLETE',f'数据完整范围为 {earliest} 至 {latest}，请调整日期，系统不会静默缩短范围')
    mature_end=c.end; warnings=[]
    if c.metric in ('retention_d7','activation_rate'):
        maturity_days=7 if c.metric=='retention_d7' else 6
        mature_end=min(c.end,latest-timedelta(days=maturity_days))
        if mature_end<c.start: raise QueryError('IMMATURE_COHORT','请求范围内没有观察期完整的注册队列')
        if mature_end<c.end: warnings.append(f'未成熟队列已单列排除；实际注册队列范围 {c.start} 至 {mature_end}')
    start,end=literal(c.start),literal(mature_end)
    dims=list(c.dimensions)
    filters=[]
    if c.channel: filters.append('ch.channel_name = '+literal(c.channel))
    if c.category: filters.append('ch.category = '+literal(c.category))
    extra=' AND '+' AND '.join(filters) if filters else ''
    if c.metric=='cpa':
        # Aggregate independent facts before joining. Spend never joins raw activations.
        keys=['ch.channel_name AS channel'] if 'channel' in dims else []
        if 'date' in dims: keys.insert(0,'s.event_date AS date')
        spend_cols=', '.join(keys+['SUM(s.amount) AS amount'])
        keys_a=['ch.channel_name AS channel'] if 'channel' in dims else []
        if 'date' in dims: keys_a.insert(0,'a.event_date AS date')
        act_cols=', '.join(keys_a+['COUNT(DISTINCT a.user_id) AS people'])
        order=[x for x in ['date','channel'] if x in dims]
        group=' GROUP BY '+','.join(order) if order else ''
        join=' AND '.join(f's.{x}=a.{x}' for x in order) or '1=1'
        merged=', '.join([f'COALESCE(s.{x},a.{x}) AS {x}' for x in order]+['s.amount AS numerator','a.people AS denominator','s.amount * 1.0 / NULLIF(a.people,0) AS value'])
        sql=f'''WITH s AS (SELECT {spend_cols} FROM spend s JOIN channels ch ON s.channel_id=ch.channel_id WHERE s.event_date BETWEEN {start} AND {end}{extra}{group}),
        a AS (SELECT {act_cols} FROM activations a JOIN users u ON a.user_id=u.user_id JOIN channels ch ON u.channel_id=ch.channel_id WHERE a.event_date BETWEEN {start} AND {end} AND a.status='success'{extra}{group})
        SELECT {merged} FROM s FULL OUTER JOIN a ON {join}'''
        if order: sql+=' ORDER BY '+','.join(order)
    else:
        when='a.event_date' if c.metric=='activations' else 'u.registration_date'
        selection={'date':when+' AS date','channel':'ch.channel_name AS channel','os':'u.os AS os'}
        prefix=[selection[x] for x in dims]
        if c.metric=='registrations':
            source='users u JOIN channels ch ON u.channel_id=ch.channel_id'
            measure='COUNT(DISTINCT u.user_id) AS value'; predicate=''
        elif c.metric=='activations':
            source='activations a JOIN users u ON a.user_id=u.user_id JOIN channels ch ON u.channel_id=ch.channel_id'
            measure='COUNT(DISTINCT a.user_id) AS value'; predicate=" AND a.status='success'"
        else:
            source='users u JOIN channels ch ON u.channel_id=ch.channel_id'
            if c.metric=='activation_rate':
                exists="EXISTS(SELECT 1 FROM activations ax WHERE ax.user_id=u.user_id AND ax.status='success' AND ax.event_date BETWEEN u.registration_date AND DATE(u.registration_date,'+6 days'))"
            else:
                exists="EXISTS(SELECT 1 FROM activity ax WHERE ax.user_id=u.user_id AND ax.event_date=DATE(u.registration_date,'+7 days'))"
            num=f'SUM(CASE WHEN {exists} THEN 1 ELSE 0 END)'
            measure=f'{num} AS numerator, COUNT(u.user_id) AS denominator, {num} * 1.0 / NULLIF(COUNT(u.user_id),0) AS value'; predicate=''
        sql=f"SELECT {', '.join(prefix+[measure])} FROM {source} WHERE {when} BETWEEN {start} AND {end}{predicate}{extra}"
        if dims: sql+=' GROUP BY '+','.join(dims)+' ORDER BY '+','.join(dims)
    return sql,{'effective_start':str(c.start),'effective_end':str(mature_end),'warnings':warnings}

def validate_sql(sql:str,expected:str|None=None):
    try: statements=sqlglot.parse(sql,read='sqlite')
    except sqlglot.errors.ParseError as exc: raise QueryError('SQL_SYNTAX','SQL 解析失败') from exc
    if len(statements)!=1 or not isinstance(statements[0],exp.Select):
        raise QueryError('READ_ONLY','仅允许单条受控 SELECT 查询')
    tree=statements[0]
    if tree.find(exp.Star): raise QueryError('WILDCARD','不允许星号列，需明确查询字段')
    forbidden=(exp.Insert,exp.Delete,exp.Update,exp.Create,exp.Drop,exp.Command,exp.Into)
    if any(tree.find(x) for x in forbidden): raise QueryError('READ_ONLY','检测到不允许的操作')
    ctes={x.alias for x in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if t.db or t.catalog or (t.name not in TABLES and t.name not in ctes):
            raise QueryError('TABLE_DENIED','未知或未授权的数据表')
    for f in tree.find_all(exp.Func):
        if isinstance(f,exp.Anonymous) and f.name.lower() not in ('date',):
            raise QueryError('FUNCTION_DENIED','不允许未登记的数据库函数')
    try:
        qualify(tree.copy(),dialect='sqlite',schema={k:{c:'TEXT' for c in t['columns']} for k,t in TABLES.items()},validate_qualify_columns=True)
    except Exception as exc: raise QueryError('SCHEMA_BINDING','字段不存在、歧义或无法绑定') from exc
    if expected is not None:
        # Explicitly constrained to canonical metric templates, not a proof of arbitrary SQL equivalence.
        canonical=lambda t:t.sql(dialect='sqlite',normalize=True,comments=False)
        if canonical(tree)!=canonical(sqlglot.parse_one(expected,read='sqlite')):
            raise QueryError('CONTRACT_MISMATCH','SQL 与已登记指标模板不一致，不能跳过过滤、去重或队列规则')
    return tree

class Warehouse:
    def __init__(self,settings): self.settings=settings
    @property
    def watermark(self): return json.loads((self.settings.data_dir/'watermark.json').read_text())
    def query(self,contract:dict,sql:str|None=None):
        c=Contract.model_validate(contract)
        expected,meta=compile_query(c,self.watermark)
        sql=sql or expected
        validate_sql(sql,expected)
        db=sqlite3.connect((self.settings.data_dir/'warehouse.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        started=time.monotonic()
        def authorize(action,arg1,arg2,dbname,source):
            if action==sqlite3.SQLITE_READ and arg1 not in TABLES: return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        db.set_progress_handler(lambda:int(time.monotonic()-started>self.settings.query_timeout),1000)
        try:
            cursor=db.execute(sql)
            rows=[dict(r) for r in cursor.fetchmany(self.settings.max_rows+1)]
            truncated=len(rows)>self.settings.max_rows
            return {'sql':sql,'contract':c.model_dump(mode='json'),'rows':rows[:self.settings.max_rows],
                'columns':[x[0] for x in cursor.description],'row_count':min(len(rows),self.settings.max_rows),
                'is_truncated':truncated,'schema_version':SCHEMA_VERSION,'snapshot':self.watermark['snapshot'],
                'watermark':self.watermark['complete_through'],'query_ms':round((time.monotonic()-started)*1000,2),**meta}
        except sqlite3.OperationalError as exc:
            raise QueryError('QUERY_TIMEOUT' if 'interrupt' in str(exc).lower() else 'EXECUTION','查询超时或执行失败') from exc
        finally: db.close()
