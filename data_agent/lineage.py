"""加工边与 Join 图分开维护，返回有实际 SQL 的可追溯证据。"""

from .catalog import CATALOG, TABLES
from .config import ROOT
from .metadata_loader import digest


def trace_lineage(table):
    if table not in TABLES:
        raise ValueError("表未登记")
    models = {m["name"]: m for m in CATALOG["models"]}
    nodes = set()
    edges = []
    active = set()

    def visit(name):
        if name in active:
            raise ValueError("加工血缘存在循环")
        if name in nodes:
            return
        active.add(name)
        nodes.add(name)
        if name in models:
            m = models[name]
            sql = (ROOT / m["sql_path"]).read_text()
            for upstream in m["upstream"]:
                edges.append(
                    {
                        "type": "derives_from",
                        "upstream_model": upstream,
                        "downstream_model": name,
                        "sql_path": m["sql_path"],
                        "sql_version": digest(sql),
                        "sql": sql,
                    }
                )
                visit(upstream)
        active.remove(name)

    visit(table)
    # 关键指标字段的人工核对表达式；不声称自动列级 SQL 解析。
    expressions = {
        "ads_channel_growth_month": {
            "registrations": "COUNT(DISTINCT dwd_user_registration.user_id) 按月、渠道",
            "active_users": "COUNT(DISTINCT dwd_user_activity.user_id) 按完整自然月、渠道",
        },
        "dws_channel_activity_day": {
            "active_users": "COUNT(DISTINCT dwd_user_activity.user_id) 按日、渠道；不可跨日相加"
        },
        "dws_channel_registration_day": {
            "registrations": "COUNT(DISTINCT dwd_user_registration.user_id) 按首次注册日、渠道"
        },
    }
    return {
        "table": table,
        "nodes": [{"table": n, "layer": TABLES[n]["layer"]} for n in sorted(nodes)],
        "edges": edges,
        "field_expressions": expressions.get(table, {}),
        "note": "加工血缘来自登记 ETL SQL；Join 关系另行维护。SOURCE 表由模拟生成器产生。",
    }
