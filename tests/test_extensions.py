"""新增功能的真实 SQLite/MCP 执行与模型响应桩测试；不是模型效果评测。"""

import asyncio
import json
import sqlite3
import uuid
from collections import defaultdict
import pytest
from data_agent.catalog import TABLES
from data_agent.metadata_search import MetadataSearchRequest, search
from data_agent.warehouse_build import build, read_profiles
from data_agent.source_planner import plan_source
from data_agent.sql_agent import QuerySpec, execute_general, validate_general
from data_agent.sql_repair import execute_with_repair
from data_agent.query import QueryError
from data_agent.lineage import trace_lineage


def ask(engine, q, session=None):
    task = asyncio.run(engine.ask(q, session, uuid.uuid4().hex))
    assert task["state"] == "COMPLETED", task.get("error", task)
    return task


def test_catalog_pagination(engine):
    expected = sorted(t for t, v in TABLES.items() if "user_id" in v["columns"])
    req = MetadataSearchRequest(field_terms=["user_id"], page_size=2)
    got = []
    while True:
        page = search(req)
        got.extend(r["table"] for r in page["rows"])
        if not page["next_cursor"]:
            break
        req.cursor = page["next_cursor"]
    assert got == expected
    cursor = search(MetadataSearchRequest(field_terms=["user_id"], page_size=2))[
        "next_cursor"
    ]
    with pytest.raises(ValueError, match="目录已变化"):
        search(
            MetadataSearchRequest(
                field_terms=["channel_id"], page_size=2, cursor=cursor
            )
        )
    assert search(MetadataSearchRequest(field_terms=["does_not_exist"]))["total"] == 0
    assert (
        search(
            MetadataSearchRequest(field_terms=["user_id"]), allowed_tables=["users"]
        )["total"]
        == 1
    )


def test_metadata_followup_and_lineage(engine):
    first = ask(engine, "哪些表包含 user_id 字段")
    next_task = ask(engine, "只看 DWD", first["session_id"])
    both = ask(engine, "还要有 channel_id", first["session_id"])
    assert {r["table"] for r in both["metadata"]["rows"]} == {
        "dwd_user_registration",
        "dwd_user_activity",
    }
    assert both["metadata"]["request"]["field_terms"] == ["user_id", "channel_id"]
    direct = ask(engine, "找 DWD 层同时包含 user_id 和 channel_id 的表")
    assert direct["metadata"]["total"] == 2
    trace = trace_lineage("ads_channel_growth_month")
    assert any(e["upstream_model"] == "users" for e in trace["edges"])
    assert all(
        e["type"] == "derives_from" and "CREATE TABLE" in e["sql"]
        for e in trace["edges"]
    )
    assert trace["field_expressions"]["active_users"].startswith("COUNT(DISTINCT")


def test_build_idempotent_and_profiles(engine):
    root = engine.settings.data_dir
    with sqlite3.connect(root / "warehouse.sqlite") as db:
        before = {
            t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES
        }
        original = list(db.execute("SELECT * FROM users ORDER BY user_id"))
    state = build(root, force=True)
    with sqlite3.connect(root / "warehouse.sqlite") as db:
        after = {
            t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES
        }
        assert list(db.execute("SELECT * FROM users ORDER BY user_id")) == original
    assert before == after and len(after) == 23
    profile = read_profiles(root)["tables"]["plays"]["watch_seconds"]
    assert profile["sampling"] == "full" and profile["null_rate"] > 0
    assert profile["distinct_count"] <= 181


def test_source_selection_and_independent_active_reference(engine):
    s = engine.warehouse_state
    assert (
        plan_source("registrations", "2026-08-01", "2026-08-31", ["channel"], s)[
            "layer"
        ]
        == "ADS"
    )
    assert (
        plan_source("registrations", "2026-08-02", "2026-08-20", ["channel"], s)[
            "layer"
        ]
        == "DWS"
    )
    assert (
        plan_source("registrations", "2026-08-01", "2026-08-31", ["channel", "os"], s)[
            "layer"
        ]
        == "DWD"
    )
    assert (
        plan_source("active_users", "2026-08-02", "2026-08-20", ["channel"], s)["layer"]
        == "DWD"
    )
    assert (
        plan_source("active_users", "2026-09-01", "2026-09-20", ["channel"], s)["layer"]
        == "DWD"
    )
    t = ask(engine, "2026-08-02 至 2026-08-20 各渠道活跃人数")
    with sqlite3.connect(engine.settings.data_dir / "warehouse.sqlite") as db:
        channels = dict(db.execute("SELECT channel_id,channel_name FROM channels"))
        users = {
            uid: ch for uid, ch in db.execute("SELECT user_id,channel_id FROM users")
        }
        counts = defaultdict(set)
        for uid, day in db.execute("SELECT user_id,event_date FROM activity"):
            if "2026-08-02" <= day <= "2026-08-20":
                counts[channels[users[uid]]].add(uid)
    assert {r["channel"]: r["value"] for r in t["query_result"]["rows"]} == {
        ch: len(ids) for ch, ids in counts.items()
    }


@pytest.mark.parametrize(
    "q",
    [
        "上个月搜索渠道注册用户明细",
        "上个月内容播放排行前10",
        "上个月各投放计划成本",
        "上个月各渠道注册人数排行前3",
    ],
)
def test_offline_new_queries(engine, q):
    t = ask(engine, q)
    assert t["generation_mode"] == "offline_demo_template"
    assert t["query_result"]["rows"]
    assert "artifact_id" not in t  # 通用结果不混入固定指标分析/复用。


def test_content_and_spend_independent_reference(engine):
    t = ask(engine, "上个月内容播放排行前10")
    with sqlite3.connect(engine.settings.data_dir / "warehouse.sqlite") as db:
        counts = defaultdict(int)
        for cid, day in db.execute("SELECT content_id,event_date FROM plays"):
            if "2026-08-01" <= day <= "2026-08-31":
                counts[cid] += 1
        expected = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:10]
        assert [
            (r["content_id"], r["play_count"]) for r in t["query_result"]["rows"]
        ] == expected
        base = {
            (day, ch): amount for day, ch, amount in db.execute("SELECT * FROM spend")
        }
        plans = defaultdict(float)
        for day, pid, amount in db.execute("SELECT * FROM plan_spend"):
            plans[(day, pid // 10)] += amount
        assert dict(plans) == pytest.approx(base)
        # 互动统计不得因曝光/播放 Join 扇出而倍增。
        assert (
            db.execute("SELECT SUM(interactions) FROM dws_content_day").fetchone()[0]
            == db.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        )


def spec():
    return QuerySpec(
        question="指定注册日期的用户",
        tables=["users"],
        filters=[
            {
                "column": "users.registration_date",
                "operator": "between",
                "values": ["2026-08-01", "2026-08-31"],
            }
        ],
        projections=["users.user_id"],
        limit=3,
    )


def test_sql_constraints_and_truncation(engine):
    good = "SELECT u.user_id FROM users u WHERE u.registration_date BETWEEN '2026-08-01' AND '2026-08-31'"
    r = execute_general(engine.settings, good, spec())
    assert r["is_truncated"] and r["row_count"] == 3
    for bad in [
        good.replace("BETWEEN", "IS NULL OR u.registration_date BETWEEN"),
        "SELECT user_id FROM users",
        good.replace("u.user_id", "COUNT(u.user_id)"),
        good + " UNION ALL SELECT user_id FROM users",
    ]:
        with pytest.raises(QueryError):
            validate_general(bad, spec())
    for bad in [
        "DELETE FROM users",
        "SELECT user_id FROM sqlite_master",
        "SELECT load_extension('x') FROM users",
        "SELECT * FROM users",
        "SELECT readfile('/etc/passwd') FROM users",
        "SELECT u.user_id FROM users u; SELECT 1",
    ]:
        with pytest.raises(QueryError):
            validate_general(bad, spec())


def test_cte_window_and_comparison_sql(engine):
    s = QuerySpec(question="按日期趋势与环比", tables=["users"], limit=5000)
    sql = """WITH daily AS (SELECT registration_date,COUNT(user_id) n FROM users GROUP BY registration_date)
    SELECT registration_date,n,LAG(n) OVER(ORDER BY registration_date) AS previous_n FROM daily ORDER BY registration_date"""
    r = execute_general(engine.settings, sql, s)
    assert r["rows"][1]["previous_n"] == r["rows"][0]["n"]
    assert not r["is_truncated"]


def test_repair_real_execution_and_immutable_contract(engine):
    s = spec()
    bad = "SELECT wrong_column FROM users WHERE registration_date BETWEEN '2026-08-01' AND '2026-08-31'"
    good = "SELECT user_id FROM users WHERE registration_date BETWEEN '2026-08-01' AND '2026-08-31'"

    async def execute(sql, locked):
        return execute_general(engine.settings, sql, locked)

    async def propose(locked, feedback):
        assert feedback["code"] == "SCHEMA_BINDING"
        locked.filters = []  # 即使适配器修改收到的对象，执行仍使用锁定的原契约。
        return good

    r, trace = asyncio.run(execute_with_repair(s, bad, execute, propose))
    assert r["rows"] and len(trace) == 2 and s.filters

    async def remove_filters(locked, feedback):
        return "SELECT user_id FROM users"

    events = []
    with pytest.raises(QueryError):
        asyncio.run(execute_with_repair(s, bad, execute, remove_filters, trace=events))
    assert len(events) <= 3 and events[-1]["code"] == "CONTRACT_MISMATCH"
    called = []

    async def never(locked, feedback):
        called.append(1)
        return good

    with pytest.raises(QueryError):
        asyncio.run(execute_with_repair(s, "DELETE FROM users", execute, never))
    assert not called


def test_real_extended_mcp(engine):
    engine.settings.transport = "stdio"
    meta = ask(engine, "哪些表包含 user_id 字段")
    assert meta["metadata"]["total"] > 3
    result = ask(engine, "上个月搜索渠道注册用户明细")
    assert result["query_result"]["rows"]
    lineage = ask(engine, "ads_channel_growth_month 的上游是什么")
    assert lineage["lineage"][0]["edges"]


def test_live_generation_adapter_with_fake_model(engine, monkeypatch):
    engine.settings.mode = "live"

    def call(messages, name, schema):
        if name == "plan_sql":
            return {
                "question": "内容排行",
                "tables": ["contents"],
                "projections": ["contents.content_id"],
                "sort_by": ["content_id"],
                "limit": 10,
            }, {}
        if name == "propose_sql":
            return {
                "sql": "SELECT content_id FROM contents ORDER BY content_id LIMIT 10"
            }, {}
        raise AssertionError(name)

    monkeypatch.setattr(engine.planner.model, "call", call)
    t = ask(engine, "内容排行")
    assert t["generation_mode"] == "live_model" and len(t["query_result"]["rows"]) == 10


def test_api_extensions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from data_agent.api import app

    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("AGENT_TRANSPORT", "local")
    with TestClient(app) as client:
        r = client.post(
            "/api/metadata/search", json={"field_terms": ["user_id"], "layer": "DWD"}
        )
        assert r.status_code == 200 and r.json()["total"] == 2
        assert (
            client.post("/api/metadata/search", json={"page_size": 0}).status_code
            == 422
        )
        r = client.post(
            "/api/query/sql",
            json={
                "sql": "SELECT user_id FROM users",
                "spec": {"question": "明细", "tables": ["users"], "limit": 2},
            },
        )
        assert r.status_code == 200 and r.json()["is_truncated"]
        denied = client.post(
            "/api/query/sql",
            json={
                "sql": "DELETE FROM users",
                "spec": {"question": "错误", "tables": ["users"]},
            },
        )
        assert denied.status_code == 422
        assert client.get("/api/lineage/ads_channel_growth_month").json()["edges"]
        assert client.get("/api/metadata/profiles").json()["tables"]["users"]


def test_selected_source_explanation_and_active_followup(engine):
    first = ask(engine, "上个月各渠道注册人数")
    assert first["report"]["source_plan"]["layer"] == "ADS"
    why = ask(engine, "为什么用了这张表", first["session_id"])
    assert why["output"]["rows"][0]["layer"] == "ADS"
    active = ask(engine, "2026-08-02 至 2026-08-20 各渠道活跃人数")
    follow = ask(engine, "再按操作系统拆开", active["session_id"])
    assert follow["source_plan"]["start"] == "2026-08-02"
    assert follow["source_plan"]["dimensions"] == ["channel", "os"]
    assert follow["source_plan"]["layer"] == "DWD"
