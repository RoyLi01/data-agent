"""Structured field views and query-specific Schema subgraphs (no graph database required)."""

from collections import deque
from .catalog import TABLES, METRICS, RELATIONS, chunks, required_ids

ALIASES = {
    "user_id": ["用户ID", "用户标识", "去重人数"],
    "registration_date": ["注册时间", "注册日期", "cohort"],
    "event_date": ["事件时间", "统计日期"],
    "status": ["成功状态", "激活状态"],
    "channel_id": ["渠道ID", "渠道关联键"],
    "channel_name": ["渠道名称", "渠道"],
    "category": ["渠道分类", "信息流", "搜索"],
    "amount": ["花费", "投放费用", "金额"],
    "os": ["操作系统", "设备系统"],
}


def index_documents():
    """按字段建立关键词、语义、重排三种视图，保留共同的文档 ID。"""
    docs = []
    for doc in chunks():
        d = dict(doc)
        if d["kind"] == "field":
            table, column = d["id"][6:].split(".")
            metric_names = [
                m["name"] for m in METRICS.values() if table + "." + column in m["deps"]
            ]
            relations = [
                r for r in RELATIONS if table + "." + column in (r["left"], r["right"])
            ]
            d["table"] = table
            d["field"] = column
            # 同一字段的三个表示分别服务于精确匹配、语义匹配和联合重排。
            d["keyword_text"] = " ".join(
                [table, column, *ALIASES.get(column, []), *metric_names]
            )
            d["semantic_text"] = (
                f"{TABLES[table]['description']}。{column} 表示 {TABLES[table]['columns'][column]}。用于 {'、'.join(metric_names)}。"
            )
            d["rerank_text"] = (
                f"字段 {table}.{column}；含义 {TABLES[table]['columns'][column]}；所属表 {TABLES[table]['description']}；表粒度 {TABLES[table]['grain']}；业务规则："
                + "；".join(
                    m["rule"]
                    for m in METRICS.values()
                    if table + "." + column in m["deps"]
                )
                + f"；允许关联 {relations}"
            )
        else:
            d["keyword_text"] = d["text"]
            d["semantic_text"] = d["text"]
            d["rerank_text"] = d["text"]
        d["text"] = d["rerank_text"]
        docs.append(d)
    return docs


def schema_graph(selected, contract=None):
    """根据已登记关系连接候选表，补齐中间表和 Join 键。

    图只提供结构约束，不替代 SQL 业务口径校验。"""
    wanted = set()
    for d in selected:
        if d["id"].startswith("field:"):
            wanted.add(d["id"][6:].split(".")[0])
        if d["id"].startswith("table:"):
            wanted.add(d["id"][6:])
    if contract:
        wanted.update(x[6:] for x in required_ids(contract) if x.startswith("table:"))
    # 仅使用登记关系建图；查找路径时允许沿关系双向遍历。
    adjacency = {t: [] for t in TABLES}
    for r in RELATIONS:
        a, b = r["left"].split(".")[0], r["right"].split(".")[0]
        adjacency[a].append(b)
        adjacency[b].append(a)
    connected = set(wanted)
    paths = []
    for start in sorted(wanted):
        for end in sorted(wanted):
            if start >= end:
                continue
            # BFS 找到连接两张候选表的路径，并保留沿途的中间表。
            queue = deque([(start, [start])])
            seen = {start}
            while queue:
                current, path = queue.popleft()
                if current == end:
                    paths.append(path)
                    connected.update(path)
                    break
                for nxt in sorted(adjacency[current]):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append((nxt, path + [nxt]))
    edges = [
        dict(r)
        for r in RELATIONS
        if r["left"].split(".")[0] in connected
        and r["right"].split(".")[0] in connected
    ]
    fields = {d["id"][6:] for d in selected if d["id"].startswith("field:")}
    for e in edges:
        fields.update([e["left"], e["right"]])
    return {
        "nodes": [
            {
                "table": t,
                "grain": TABLES[t]["grain"],
                "description": TABLES[t]["description"],
            }
            for t in sorted(connected)
        ],
        "edges": edges,
        "paths": paths,
        "required_join_fields": sorted(fields),
        "constraints": [
            "事实表先按目标粒度聚合；经用户表连接两个事实可能放大行数",
            "关联图约束仅描述允许路径；指标计算仍须通过查询契约校验",
        ],
    }
