"""为本地模拟库增加场景与可重建 ETL；不修改原五表。"""

import calendar
import hashlib
import json
import random
import sqlite3
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from .catalog import TABLES, CATALOG, SCHEMA_VERSION
from .config import ROOT
from .metadata_loader import digest

SOURCE_TABLES = [
    "ad_plans",
    "plan_spend",
    "acquisitions",
    "creators",
    "contents",
    "exposures",
    "plays",
    "interactions",
]
OWNED = SOURCE_TABLES + [m["name"] for m in CATALOG["models"]]


def build(root, force=False):
    """版本不变时直接返回；重建只替换本模块拥有的表，事务失败则回滚。"""
    root = Path(root)
    watermark = json.loads((root / "watermark.json").read_text())
    version = digest(
        [
            SCHEMA_VERSION,
            watermark,
            Path(__file__).read_text(),
            [(ROOT / m["sql_path"]).read_text() for m in CATALOG["models"]],
        ]
    )
    with sqlite3.connect(root / "warehouse.sqlite") as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS _agent_build(version TEXT, payload TEXT)"
        )
        previous = db.execute("SELECT version,payload FROM _agent_build").fetchone()
        if previous and previous[0] == version and not force:
            return json.loads(previous[1])
        if not previous:
            existing = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if existing.intersection(OWNED):
                raise ValueError("扩展表名已存在且不属于构建器；请使用独立数据目录")
        for name in reversed(OWNED):
            db.execute(f'DROP TABLE IF EXISTS "{name}"')
        for name in SOURCE_TABLES:
            columns = ",".join(
                f'"{c}" {f["type"]}' for c, f in TABLES[name]["fields"].items()
            )
            key = ",".join(TABLES[name]["primary_key"])
            db.execute(f'CREATE TABLE "{name}" ({columns}, PRIMARY KEY ({key}))')
        seed_scenarios(db, watermark)
        start = date.fromisoformat(watermark["start"])
        end = date.fromisoformat(watermark["complete_through"])
        month_end = (
            end
            if end.day == calendar.monthrange(end.year, end.month)[1]
            else end.replace(day=1) - timedelta(days=1)
        )
        month_start = (
            start
            if start.day == 1
            else (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        )
        params = {
            "complete_month_end": str(month_end),
            "complete_month_start": str(month_start),
        }
        for model in CATALOG["models"]:
            db.execute((ROOT / model["sql_path"]).read_text(), params)
            name = model["name"]
            key = ",".join(TABLES[name]["primary_key"])
            db.execute(f'CREATE UNIQUE INDEX "ix_grain_{name}" ON "{name}" ({key})')
        validate_data(db)
        state = {
            "version": version,
            "schema_version": SCHEMA_VERSION,
            "watermark": watermark,
            "complete_month_start": str(month_start),
            "complete_month_end": str(month_end),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "tables": len(TABLES),
        }
        db.execute("DELETE FROM _agent_build")
        db.execute(
            "INSERT INTO _agent_build VALUES (?,?)", (version, json.dumps(state))
        )
        # 画像与版本同事务持久化；不暴露为业务查询表。
        db.execute("CREATE TABLE IF NOT EXISTS _agent_profiles(payload TEXT)")
        profiles = collect_profiles(db, state)
        db.execute("DELETE FROM _agent_profiles")
        db.execute(
            "INSERT INTO _agent_profiles VALUES (?)",
            (json.dumps(profiles, ensure_ascii=False),),
        )
        return state


def seed_scenarios(db, watermark):
    """可复现模拟规则：渠道下两计划；注册唯一归因，约十分之一无归因。"""
    rng = random.Random(20260927)
    plans = [
        (channel * 10 + i, channel, f"渠道{channel}计划{i}")
        for channel in range(1, 5)
        for i in (1, 2)
    ]
    db.executemany("INSERT INTO ad_plans VALUES (?,?,?)", plans)
    spends = []
    for day, channel, amount in db.execute(
        "SELECT event_date,channel_id,amount FROM spend ORDER BY event_date,channel_id"
    ):
        first = round(amount * 0.6, 2)
        spends.extend(
            [
                (day, channel * 10 + 1, first),
                (day, channel * 10 + 2, round(amount - first, 2)),
            ]
        )
    db.executemany("INSERT INTO plan_spend VALUES (?,?,?)", spends)
    users = list(
        db.execute(
            "SELECT user_id,registration_date,channel_id FROM users ORDER BY user_id"
        )
    )
    db.executemany(
        "INSERT INTO acquisitions VALUES (?,?,?)",
        [(uid, ch * 10 + 1 + uid % 2, day) for uid, day, ch in users if uid % 10],
    )
    db.executemany(
        "INSERT INTO creators VALUES (?,?)", [(i, f"创作者{i}") for i in range(1, 9)]
    )
    db.executemany(
        "INSERT INTO contents VALUES (?,?,?,?,?)",
        [
            (
                i,
                1 + i % 8,
                f"内容{i}",
                ("知识", "生活", "运动")[i % 3],
                watermark["start"],
            )
            for i in range(1, 31)
        ],
    )
    exps = []
    plays = []
    events = []
    eid = pid = iid = 0
    for uid, day, ch in users:
        # 同一日事件便于验证转化窗口；部分内容没有播放，观看时长有空值。
        for _ in range(1 + uid % 3):
            eid += 1
            cid = 1 + rng.randrange(30)
            exps.append((eid, cid, uid, day))
            if cid == 30 or rng.random() > 0.65:
                continue
            for _ in range(2 if eid % 13 == 0 else 1):
                pid += 1
                plays.append(
                    (
                        pid,
                        eid,
                        cid,
                        uid,
                        day,
                        None if pid % 17 == 0 else rng.randint(1, 180),
                    )
                )
                if pid % 3 == 0:
                    iid += 1
                    events.append((iid, pid, day, "like"))
                if pid % 7 == 0:
                    iid += 1
                    events.append((iid, pid, day, "comment"))
    db.executemany("INSERT INTO exposures VALUES (?,?,?,?)", exps)
    db.executemany("INSERT INTO plays VALUES (?,?,?,?,?,?)", plays)
    db.executemany("INSERT INTO interactions VALUES (?,?,?,?)", events)


def validate_data(db):
    for relation in CATALOG["relations"]:
        lt, lc = relation["left"].split(".")
        rt, rc = relation["right"].split(".")
        n = db.execute(
            f'SELECT COUNT(*) FROM "{lt}" l LEFT JOIN "{rt}" r ON l."{lc}"=r."{rc}" WHERE l."{lc}" IS NOT NULL AND r."{rc}" IS NULL'
        ).fetchone()[0]
        if n:
            raise ValueError(f"关联键检查失败：{lt}.{lc}")
    mismatch = db.execute(
        """SELECT COUNT(*) FROM spend s JOIN (SELECT event_date,p.plan_id/10 AS channel_id,SUM(amount) amount FROM plan_spend p GROUP BY 1,2) a ON s.event_date=a.event_date AND s.channel_id=a.channel_id WHERE ABS(s.amount-a.amount)>0.001"""
    ).fetchone()[0]
    if mismatch:
        raise ValueError("计划花费与渠道花费不一致")


def collect_profiles(db, state):
    out = {}
    for name, table in TABLES.items():
        total = db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        fields = {}
        for col, definition in table["fields"].items():
            defined, distinct, low, high = db.execute(
                f'SELECT COUNT("{col}"),COUNT(DISTINCT "{col}"),MIN("{col}"),MAX("{col}") FROM "{name}"'
            ).fetchone()
            samples = [
                r[0]
                for r in db.execute(
                    f'SELECT DISTINCT "{col}" FROM "{name}" WHERE "{col}" IS NOT NULL ORDER BY "{col}" LIMIT 5'
                )
            ]
            distribution = [
                {"value": r[0], "count": r[1]}
                for r in db.execute(
                    f'SELECT "{col}",COUNT(*) n FROM "{name}" GROUP BY "{col}" ORDER BY n DESC,"{col}" LIMIT 10'
                )
            ]
            fields[col] = {
                "type": definition["type"],
                "samples": samples,
                "null_rate": (total - defined) / total if total else None,
                "distinct_count": distinct,
                "min": low,
                "max": high,
                "top_values": distribution,
                "row_count": total,
                "sampling": "full",
            }
        out[name] = fields
    return {
        "version": state["version"],
        "schema_version": SCHEMA_VERSION,
        "collected_at": state["built_at"],
        "tables": out,
    }


def read_state(root):
    with sqlite3.connect(Path(root) / "warehouse.sqlite") as db:
        row = db.execute("SELECT payload FROM _agent_build").fetchone()
        state = json.loads(row[0])
        watermark = json.loads((Path(root) / "watermark.json").read_text())
        if state["watermark"] != watermark or state["schema_version"] != SCHEMA_VERSION:
            raise ValueError("源快照或元数据已变化，请重建分层模型并重启服务")
        return state


def read_profiles(root):
    with sqlite3.connect(Path(root) / "warehouse.sqlite") as db:
        row = db.execute("SELECT payload FROM _agent_profiles").fetchone()
        return json.loads(row[0])


if __name__ == "__main__":
    import argparse
    from .seed import seed
    from .config import Settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    root = Settings.env().data_dir
    seed(root)
    print(json.dumps(build(root, args.rebuild), ensure_ascii=False, indent=2))
