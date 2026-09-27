"""对注册/周期活跃选择合适层级，拒绝把日去重计数求和为月活。"""

from datetime import date
import calendar
from .catalog import TABLES
from .query import literal, QueryError


def plan_source(metric, start, end, dimensions, state, allowed_tables=None):
    if metric not in {"registrations", "active_users"}:
        raise ValueError("选表器只覆盖注册与周期活跃")
    allowed = set(TABLES) if allowed_tables is None else set(allowed_tables)
    wm = state["watermark"]
    if start < wm["start"] or end > wm["complete_through"]:
        raise QueryError("DATA_INCOMPLETE", "请求超出数据完整范围")
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    full_month = (
        first.day == 1
        and last.day == calendar.monthrange(last.year, last.month)[1]
        and start[:7] == end[:7]
    )
    candidates = [
        "ads_channel_growth_month",
        "dws_channel_registration_day"
        if metric == "registrations"
        else "dws_channel_activity_day",
        "dwd_user_registration" if metric == "registrations" else "dwd_user_activity",
    ]
    decisions = []
    chosen = None
    for table in candidates:
        reason = []
        if table not in allowed or "dim_channel" not in allowed:
            reason.append("超出登记可见范围")
        if "os" in dimensions and TABLES[table]["layer"] in {"ADS", "DWS"}:
            reason.append("缺少 OS 维度")
        if TABLES[table]["layer"] == "ADS":
            if not full_month or "date" in dimensions:
                reason.append("仅适用单个完整自然月且不按日拆分")
            if (
                start < state["complete_month_start"]
                or end > state["complete_month_end"]
            ):
                reason.append("月汇总覆盖不完整")
            if metric == "active_users" and "channel" not in dimensions:
                reason.append("汇总去重人数不跨渠道相加，使用明细保证实体去重")
        if TABLES[table]["layer"] == "DWS" and metric == "active_users":
            if start != end and "date" not in dimensions:
                reason.append("日去重人数不可相加作为周期去重人数")
            if "channel" not in dimensions:
                reason.append("跨渠道去重采用明细")
        accepted = not reason
        decisions.append(
            {
                "table": table,
                "accepted": accepted,
                "reasons": reason or ["口径、维度、粒度和覆盖匹配"],
            }
        )
        if accepted and chosen is None:
            chosen = table
    if not chosen:
        raise QueryError("NO_SOURCE", "没有满足口径与范围的数据模型")
    return {
        "metric": metric,
        "metric_version": "v1",
        "table": chosen,
        "layer": TABLES[chosen]["layer"],
        "dimensions": dimensions,
        "start": start,
        "end": end,
        "source_version": state["version"],
        "candidates": decisions,
        "fallback": chosen != candidates[0],
        "selection_rule": "先检查语义兼容，再使用登记的层级成本启发式；非实测扫描成本",
    }


def compile_source(plan, channel=None, category=None):
    table = plan["table"]
    metric = plan["metric"]
    layer = plan["layer"]
    dims = plan["dimensions"]
    time_col = (
        "registration_date"
        if table == "dwd_user_registration"
        else "month"
        if layer == "ADS"
        else "event_date"
    )
    cols = {"date": f"s.{time_col}", "channel": "ch.channel_name", "os": "s.os"}
    select = [f"{cols[d]} AS {d}" for d in dims]
    measure = (
        "COUNT(DISTINCT s.user_id)"
        if layer == "DWD"
        else "SUM(s.registrations)"
        if metric == "registrations"
        else "SUM(s.active_users)"
    )
    select.append(measure + " AS value")
    if layer == "ADS":
        pred = f"s.month = {literal(plan['start'][:7])}"
    else:
        pred = (
            f"s.{time_col} BETWEEN {literal(plan['start'])} AND {literal(plan['end'])}"
        )
    if channel:
        pred += " AND ch.channel_name = " + literal(channel)
    if category:
        pred += " AND ch.category = " + literal(category)
    sql = f"SELECT {', '.join(select)} FROM {table} s JOIN dim_channel ch ON s.channel_id=ch.channel_id WHERE {pred}"
    if dims:
        sql += (
            " GROUP BY "
            + ",".join(cols[d] for d in dims)
            + " ORDER BY "
            + ",".join(dims)
        )
    return sql
