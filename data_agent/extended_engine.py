"""新增目录、血缘和通用查询分支；原指标分析流程继续由 Engine 执行。"""

import asyncio
import json
import re
from datetime import date, timedelta
from .catalog import TABLES, METRICS
from .metadata_search import parse_search, search, MetadataSearchRequest
from .sql_agent import QuerySpec, SqlAgent
from .sql_repair import execute_with_repair
from .query import QueryError
from .source_planner import plan_source, compile_source


def latest_task(engine, task):
    with engine.store.connect() as db:
        row = db.execute(
            "SELECT payload FROM tasks WHERE session_id=? AND owner=? AND id!=? ORDER BY rowid DESC LIMIT 1",
            (task["session_id"], task["owner"], task["id"]),
        ).fetchone()
    return json.loads(row[0]) if row else {}


def request_dates(question, as_of):
    """离线演示的有限日期解析；未支持的相对日期不静默替换。"""
    today = date.fromisoformat(str(as_of))
    first = today.replace(day=1)
    start = (first - timedelta(days=1)).replace(day=1)
    end = first - timedelta(days=1)
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", question)
    if dates:
        if len(dates) > 2:
            raise ValueError("请明确一个起止日期范围")
        start = date.fromisoformat(dates[0])
        end = date.fromisoformat(dates[-1])
    elif "上周" in question:
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
    elif "本月" in question:
        start = first
        end = today - timedelta(days=1)
    elif "最近" in question:
        found = re.search(r"最近\s*(\d+)\s*天", question)
        if not found:
            raise ValueError("请使用最近 N 天或明确起止日期")
        end = today - timedelta(days=1)
        start = end - timedelta(days=int(found[1]) - 1)
    elif re.search("今年|去年|昨天|今天|上季度|本周", question):
        raise ValueError("离线演示请使用明确起止日期、上个月、上周或最近 N 天")
    if start > end:
        raise ValueError("开始日期晚于结束日期")
    return str(start), str(end)


def offline_query(question, as_of):
    """可运行的演示模板，不冒充通用模型理解；其他请求交给 live 模型。"""
    start, end = request_dates(question, as_of)
    limit_match = re.search(r"(?:前|top\s*)(\d+)", question, re.I)
    limit = int(limit_match[1]) if limit_match else 200
    table = None
    sql = None
    groups = []
    sort = []
    filters = []
    if re.search("注册.*明细|用户明细", question):
        table = "users"
        tables = ["users", "channels"]
        sql = "SELECT u.user_id,u.registration_date,ch.channel_name,u.os FROM users u JOIN channels ch ON u.channel_id=ch.channel_id"
        column = "users.registration_date"
        alias = "u.registration_date"
        if "搜索" in question:
            filters.append(
                {"column": "channels.category", "operator": "eq", "values": ["搜索"]}
            )
        if "信息流" in question:
            filters.append(
                {"column": "channels.category", "operator": "eq", "values": ["信息流"]}
            )
        for value in ["iOS", "Android"]:
            if value in question:
                filters.append(
                    {"column": "users.os", "operator": "eq", "values": [value]}
                )
        for value in ["渠道A", "渠道B", "渠道C", "自然流量"]:
            if value in question:
                filters.append(
                    {
                        "column": "channels.channel_name",
                        "operator": "eq",
                        "values": [value],
                    }
                )
        groups = []
        sort = ["u.user_id"]
    elif re.search("内容|视频|创作者", question) and re.search("播放|排行", question):
        table = "plays"
        tables = ["plays", "contents"]
        column = "plays.event_date"
        alias = "p.event_date"
        sql = "SELECT c.content_id,c.title,COUNT(p.play_id) AS play_count FROM plays p JOIN contents c ON p.content_id=c.content_id"
        groups = ["contents.content_id", "contents.title"]
        sort = ["play_count DESC", "c.content_id"]
    elif re.search("投放|计划", question) and re.search("成本|花费|效果", question):
        table = "dws_plan_day"
        tables = ["dws_plan_day", "ad_plans"]
        column = "dws_plan_day.event_date"
        alias = "d.event_date"
        sql = "SELECT p.plan_id,p.plan_name,SUM(d.amount) AS amount,SUM(d.registrations) AS registrations,SUM(d.amount)/NULLIF(SUM(d.registrations),0) AS cost FROM dws_plan_day d JOIN ad_plans p ON d.plan_id=p.plan_id"
        groups = ["ad_plans.plan_id", "ad_plans.plan_name"]
        sort = ["amount DESC", "p.plan_id"]
    elif (
        re.search("渠道", question)
        and re.search("注册", question)
        and re.search(r"排行|最多|前\d+|top", question, re.I)
    ):
        table = "users"
        tables = ["users", "channels"]
        column = "users.registration_date"
        alias = "u.registration_date"
        sql = "SELECT ch.channel_name,COUNT(DISTINCT u.user_id) AS registrations FROM users u JOIN channels ch ON u.channel_id=ch.channel_id"
        groups = ["channels.channel_name"]
        sort = ["registrations DESC", "ch.channel_name"]
    if not sql:
        raise ValueError(
            "离线通用查询仅提供注册明细、渠道注册排行、内容播放排行和投放计划成本演示；其他明细、CTE、窗口和时间对比请配置 live 模型，或使用结构化 SQL API。"
        )
    filters.insert(0, {"column": column, "operator": "between", "values": [start, end]})
    sql += f" WHERE {alias} BETWEEN '{start}' AND '{end}'"
    for f in filters[1:]:
        field = f["column"].replace("channels.", "ch.").replace("users.", "u.")
        sql += " AND " + field + " = '" + f["values"][0] + "'"
    if groups:
        aliases = {"contents": "c", "ad_plans": "p", "channels": "ch"}
        sql += " GROUP BY " + ",".join(
            aliases[g.split(".")[0]] + "." + g.split(".")[1] for g in groups
        )
    sql += " ORDER BY " + ",".join(sort)
    if limit_match:
        sql += f" LIMIT {limit}"
    return QuerySpec(
        question=question,
        tables=tables,
        filters=filters,
        group_by=groups,
        sort_by=sort,
        limit=limit,
    ), sql


def task_kind(question, previous, mode):
    if re.search("血缘|上游|加工来源|哪些表加工", question):
        return "lineage"
    if re.search("为什么.*(?:表|明细|DWS|ADS)|选表依据", question, re.I):
        return "source_explanation"
    if re.search(
        "哪些表|查表|找表|字段.*表|表.*字段|(?:包含|含有).*的表|找.*(?:DWD|DWS|ADS|ODS).*表",
        question,
    ):
        return "metadata"
    if previous.get("metadata") and re.search("只看|还要|再加|改成|下一页", question):
        return "metadata"
    if re.search("口径|怎么定义", question):
        return "metric_definition"
    if re.search("活跃人数|月活|日活|周期活跃", question):
        return "active_users"
    if previous.get("source_plan", {}).get("metric") == "active_users" and re.search(
        "再按|只看|刷新|最新", question
    ):
        return "active_users"
    if re.search(
        r"明细|排行|最多|前\d+|top\s*\d+|内容|视频|投放计划|创作者|同比|环比|窗口|CTE",
        question,
        re.I,
    ):
        return "general_sql"
    if previous.get("query_result") and re.search("图|分析|总结|解释|比较", question):
        return "general_analysis_limit"
    # 原有五指标与省略追问继续使用原规划/分析流程。
    if mode == "live" and not re.search(
        "注册|激活|留存|CPA|成本|花费|新增|图|趋势|总结|分析|只看|再按|刷新|结果",
        question,
        re.I,
    ):
        return "general_sql"
    return None


def finish(engine, task, kind, text, rows=None, **extra):
    engine.step(
        task,
        "COMPLETED",
        text,
        output={"kind": kind, "text": text, "rows": rows or []},
        **extra,
    )


async def run_extended(engine, task, budget):
    previous = latest_task(engine, task)
    q = task["question"]
    kind = task_kind(q, previous, engine.settings.mode)
    if kind is None:
        return False
    skill = engine.skills.load(
        "metadata_search"
        if kind
        in {
            "metadata",
            "lineage",
            "source_explanation",
            "metric_definition",
            "general_analysis_limit",
        }
        else "general_query"
    )
    engine.step(
        task,
        "PLANNING",
        "识别目录查询、血缘或通用 SQL 请求",
        routing={"effective": kind, "reason": "专用任务分支"},
    )
    if kind == "metadata":
        request = parse_search(q, previous.get("metadata", {}).get("request"))
        if "下一页" in q:
            cursor = previous.get("metadata", {}).get("next_cursor")
            if not cursor:
                engine.step(task, "RETRIEVING", "目录分页")
                finish(engine, task, kind, "没有下一页")
                return True
            request.cursor = cursor
        engine.step(task, "RETRIEVING", "筛选登记元数据，不使用 Top K 截断精确结果")
        if request.match_mode == "semantic":
            budget.consume(skill, "search_metadata")
            result = await asyncio.to_thread(
                search, request, engine.retriever, None, engine.profiles
            )
        else:
            budget.consume(skill, "find_tables_by_fields")
            result = await engine.gateway.call(
                "find_tables_by_fields", {"request": request.model_dump()}
            )
        label = "语义候选" if request.match_mode == "semantic" else "符合条件的表"
        finish(
            engine,
            task,
            kind,
            f"找到 {result['total']} 张{label}。" + result["scope"],
            result["rows"],
            metadata=result,
        )
        return True
    if kind in {
        "lineage",
        "source_explanation",
        "metric_definition",
        "general_analysis_limit",
    }:
        engine.step(task, "RETRIEVING", "读取登记规则和已有查询证据")
        if kind == "lineage":
            names = [n for n in TABLES if re.search(r"\b" + re.escape(n) + r"\b", q)]
            if not names:
                names = [
                    r["table"] for r in previous.get("metadata", {}).get("rows", [])
                ]
            if not names:
                plan = previous.get("source_plan") or previous.get("report", {}).get(
                    "source_plan"
                )
                if plan:
                    names = [plan["table"]]
            if not names:
                finish(engine, task, kind, "请明确要追溯的表名，或先按字段查表。")
                return True
            if len(names) > 5:
                finish(engine, task, kind, "候选表超过 5 张，请指定要追溯的表名。")
                return True
            rows = []
            for name in names:
                budget.consume(skill, "trace_lineage")
                rows.append(await engine.gateway.call("trace_lineage", {"table": name}))
            finish(
                engine,
                task,
                kind,
                "加工来源如下；SOURCE 表由模拟生成器产生。",
                rows,
                lineage=rows,
            )
        elif kind == "source_explanation":
            plan = previous.get("source_plan") or previous.get("report", {}).get(
                "source_plan"
            )
            finish(
                engine,
                task,
                kind,
                "最近任务的选表依据如下。" if plan else "最近任务没有数仓选表记录。",
                [plan] if plan else [],
            )
        elif kind == "metric_definition":
            found = [
                dict(id=k, **m)
                for k, m in METRICS.items()
                if m["name"] in q or any(a in q for a in m["aliases"])
            ]
            finish(
                engine,
                task,
                kind,
                "登记指标定义；未匹配时列出可用指标。",
                found or [dict(id=k, **m) for k, m in METRICS.items()],
            )
        else:
            finish(
                engine,
                task,
                kind,
                "这份结果属于通用 SQL 查询；当前分析函数只支持登记指标。可查看原表格与 SQL，未新增自由分析或图表能力。",
            )
        return True
    engine.step(task, "RETRIEVING", "检索 Schema 与登记关联")
    budget.consume(skill, "search_metadata")
    context = await engine.retrieve(q)
    task["retrieval"] = context
    if kind == "active_users":
        start, end = request_dates(q, engine.settings.as_of)
        dims = []
        if re.search("每天|每日|日活|趋势", q):
            dims.append("date")
        if "渠道" in q:
            dims.append("channel")
        if re.search("OS|操作系统", q, re.I):
            dims.append("os")
        old = previous.get("source_plan", {})
        if old.get("metric") == "active_users" and re.search("再按|只看|刷新|最新", q):
            if not re.search(r"\d{4}-\d{2}-\d{2}|上个月|本月|上周|最近", q):
                start, end = old["start"], old["end"]
            dims = list(dict.fromkeys(old["dimensions"] + dims))
        channel = next(
            (x for x in ["渠道A", "渠道B", "渠道C", "自然流量"] if x in q),
            old.get("channel") if old.get("metric") == "active_users" else None,
        )
        category = next((x for x in ["搜索", "信息流"] if x in q), None)
        plan = plan_source("active_users", start, end, dims, engine.warehouse_state)
        plan.update(channel=channel, category=category)
        sql = compile_source(plan, channel, category)
        spec = QuerySpec(question=q, tables=[plan["table"], "dim_channel"])
        task["source_plan"] = plan
    elif engine.settings.mode == "offline":
        spec, sql = offline_query(q, engine.settings.as_of)
        task["generation_mode"] = "offline_demo_template"
    else:
        agent = SqlAgent(engine.planner.model, skill["instructions"])
        # 问题相关 Schema 加上原始请求；目录不会执行其中的文本。
        nodes = context["schema_graph"]["nodes"]
        names = [n["table"] for n in nodes]
        metadata = {n: TABLES[n] for n in names}
        budget.consume(skill, "generate_sql")
        spec = await asyncio.to_thread(agent.specification, q, metadata)
        if spec.clarification:
            # 当前已经检索，澄清状态保存后可经原 resume 接口继续。
            engine.step(
                task,
                "NEEDS_CLARIFICATION",
                spec.clarification,
                clarification=spec.clarification,
            )
            return True
        budget.consume(skill, "generate_sql")
        sql = await asyncio.to_thread(agent.propose, spec, metadata)
        task["generation_mode"] = "live_model"
    task["query_spec"] = spec.model_dump()
    engine.step(task, "VALIDATING", "绑定登记表字段并检查只读及查询条件")
    engine.step(task, "EXECUTING", "通过 MCP 执行；修复后仍通过同一校验入口")
    events = []
    task["sql_attempts"] = events

    async def execute(sql, locked):
        budget.consume(skill, "query_sql")
        return await engine.gateway.call(
            "query_sql", {"sql": sql, "spec": locked.model_dump()}
        )

    async def propose(locked, feedback):
        if engine.settings.mode != "live":
            raise QueryError(
                "REPAIR_UNAVAILABLE", "离线模板失败；自动模型修复需要 live 模型配置"
            )
        budget.consume(skill, "generate_sql")
        metadata = {n: TABLES[n] for n in locked.tables if n in TABLES}
        return await asyncio.to_thread(
            SqlAgent(engine.planner.model, skill["instructions"]).propose,
            locked,
            metadata,
            feedback,
        )

    result, _ = await execute_with_repair(
        spec,
        sql,
        execute,
        propose,
        max_repairs=2 if engine.settings.mode == "live" else 0,
        trace=events,
    )
    task["query_result"] = result
    finish(
        engine,
        task,
        kind,
        "查询完成。" + ("结果为截断预览。" if result["is_truncated"] else ""),
        result["rows"],
        query_result=result,
    )
    return True
