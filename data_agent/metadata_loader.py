"""加载经过结构检查的目录；目录文本只能作为数据，不能授予执行权限。"""

import hashlib
import json
import re
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "metadata/catalog.json"


def load_catalog(path=CATALOG_PATH):
    payload = json.loads(Path(path).read_text())
    tables = payload["tables"]
    for name, table in tables.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError("元数据表名非法")
        if not table["columns"] or set(table["columns"]) != set(table["fields"]):
            raise ValueError(f"{name} 字段说明与类型不一致")
        for column, field in table["fields"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", column):
                raise ValueError("元数据字段名非法")
            if field["type"] not in {"TEXT", "INTEGER", "REAL"}:
                raise ValueError("未知字段类型")
        if not set(table["primary_key"]) <= set(table["columns"]):
            raise ValueError("粒度键不存在")
    for relation in payload["relations"]:
        for side in ("left", "right"):
            table, column = relation[side].split(".")
            if table not in tables or column not in tables[table]["columns"]:
                raise ValueError("关联引用不存在")
    for metric in payload["metrics"].values():
        for dep in metric["deps"]:
            table, column = dep.split(".")
            if column not in tables[table]["columns"]:
                raise ValueError("指标依赖不存在")
    return payload


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:20]
