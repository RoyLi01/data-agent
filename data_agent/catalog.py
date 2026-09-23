"""Registry is trusted configuration, not model-authored business truth."""
import hashlib, json

TABLES = {
 'users': {'description':'注册用户明细，每用户一行；渠道、操作系统为注册时快照', 'grain':'user_id', 'columns':{'user_id':'用户唯一标识','registration_date':'注册自然日','channel_id':'注册归属渠道','os':'注册时操作系统'}},
 'activations': {'description':'激活事件明细，含失败和重复成功记录', 'grain':'activation_id','columns':{'activation_id':'激活事件主键','user_id':'用户标识','event_date':'激活自然日','status':'success 或 failed；成功条件必须过滤'}},
 'activity': {'description':'用户日活事实，同一用户同一天仅一行', 'grain':'user_id,event_date','columns':{'user_id':'活跃用户','event_date':'活跃自然日'}},
 'channels': {'description':'渠道维表，每渠道一行','grain':'channel_id','columns':{'channel_id':'渠道唯一键','channel_name':'渠道名称','category':'渠道类别：信息流、搜索、自然'}},
 'spend': {'description':'渠道每日花费，人民币元；没有操作系统或用户粒度','grain':'event_date,channel_id','columns':{'event_date':'花费自然日','channel_id':'花费所属渠道','amount':'人民币花费'}}
}
RELATIONS = [
 {'left':'activations.user_id','right':'users.user_id','cardinality':'many-to-one'},
 {'left':'activity.user_id','right':'users.user_id','cardinality':'many-to-one'},
 {'left':'users.channel_id','right':'channels.channel_id','cardinality':'many-to-one'},
 {'left':'spend.channel_id','right':'channels.channel_id','cardinality':'many-to-one'}
]
METRICS = {
 'registrations':{'name':'注册用户数','aliases':['注册人数','新增注册'],'rule':'按 registration_date 统计注册用户；用户表一用户一行','unit':'人','additive_over_time':True,'deps':['users.user_id','users.registration_date']},
 'activations':{'name':'成功激活用户数','aliases':['激活人数','有效激活'],'rule':"按激活发生日筛选 status=success 后 COUNT(DISTINCT user_id)，不可跨日相加作为周期去重人数",'unit':'人','additive_over_time':False,'deps':['activations.user_id','activations.event_date','activations.status','users.user_id']},
 'activation_rate':{'name':'注册七日激活率','aliases':['激活率','转化率'],'rule':'注册日到注册后第6日成功激活的队列用户 / 同队列注册用户；仅展示观察期已完整的注册队列','unit':'比例','additive_over_time':False,'deps':['users.user_id','users.registration_date','activations.user_id','activations.event_date','activations.status']},
 'retention_d7':{'name':'注册 D7 留存率','aliases':['D7留存','七日留存','7日留存'],'rule':'注册后第7自然日活跃的队列用户 / 同队列注册用户；不是过去7日任一活跃；仅统计成熟队列','unit':'比例','additive_over_time':False,'deps':['users.user_id','users.registration_date','activity.user_id','activity.event_date']},
 'cpa':{'name':'成功激活成本','aliases':['CPA','获客成本','激活成本'],'rule':'同一日期范围渠道花费 / 该范围成功激活去重用户数；两事实分别聚合再关联；不支持操作系统拆分','unit':'元/人','additive_over_time':False,'deps':['spend.amount','spend.event_date','spend.channel_id','activations.status','activations.user_id','activations.event_date','users.user_id','users.channel_id']}
}
SCHEMA_VERSION = hashlib.sha256(json.dumps([TABLES,RELATIONS,METRICS],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]

def chunks():
    result=[]
    for name,t in TABLES.items():
        result.append({'id':'table:'+name,'kind':'table','text':f"{name} {t['description']} 粒度 {t['grain']} 字段 {' '.join(t['columns'])}"})
        for col,desc in t['columns'].items():
            result.append({'id':f'field:{name}.{col}','kind':'field','text':f"{name}.{col} {desc} 所属表 {t['description']}"})
    for key,m in METRICS.items():
        result.append({'id':'metric:'+key,'kind':'metric','text':f"{m['name']} {' '.join(m['aliases'])} {m['rule']} 必需依赖 {' '.join(m['deps'])}"})
    for r in RELATIONS:
        result.append({'id':f"relation:{r['left']}={r['right']}",'kind':'relation','text':json.dumps(r,ensure_ascii=False)})
    return result

def required_ids(contract):
    fields=set(METRICS[contract.metric]['deps'])
    if 'os' in contract.dimensions: fields.add('users.os')
    if 'channel' in contract.dimensions or contract.channel or contract.category or contract.metric=='cpa':
        fields.update(['users.channel_id','channels.channel_id','channels.channel_name'])
    if contract.category: fields.add('channels.category')
    out={'metric:'+contract.metric}
    for f in fields: out.update(['field:'+f,'table:'+f.split('.')[0]])
    for r in RELATIONS:
        if r['left'] in fields and r['right'] in fields:
            out.add(f"relation:{r['left']}={r['right']}")
    return out
