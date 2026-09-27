"""Registry is trusted configuration, not model-authored business truth."""

import hashlib, json

from .metadata_loader import load_catalog, digest

CATALOG = load_catalog()
TABLES = CATALOG["tables"]
RELATIONS = CATALOG["relations"]
METRICS = CATALOG["metrics"]
SCHEMA_VERSION = digest(CATALOG)


def chunks():
    result = []
    for name, t in TABLES.items():
        result.append(
            {
                "id": "table:" + name,
                "kind": "table",
                "text": f"{name} {t['description']} 粒度 {t['grain']} 字段 {' '.join(t['columns'])}",
            }
        )
        for col, desc in t["columns"].items():
            result.append(
                {
                    "id": f"field:{name}.{col}",
                    "kind": "field",
                    "text": f"{name}.{col} {desc} 所属表 {t['description']}",
                }
            )
    for key, m in METRICS.items():
        result.append(
            {
                "id": "metric:" + key,
                "kind": "metric",
                "text": f"{m['name']} {' '.join(m['aliases'])} {m['rule']} 必需依赖 {' '.join(m['deps'])}",
            }
        )
    for r in RELATIONS:
        result.append(
            {
                "id": f"relation:{r['left']}={r['right']}",
                "kind": "relation",
                "text": json.dumps(r, ensure_ascii=False),
            }
        )
    return result


def required_ids(contract):
    fields = set(METRICS[contract.metric]["deps"])
    if "os" in contract.dimensions:
        fields.add("users.os")
    if (
        "channel" in contract.dimensions
        or contract.channel
        or contract.category
        or contract.metric == "cpa"
    ):
        fields.update(
            ["users.channel_id", "channels.channel_id", "channels.channel_name"]
        )
    if contract.category:
        fields.add("channels.category")
    out = {"metric:" + contract.metric}
    for f in fields:
        out.update(["field:" + f, "table:" + f.split(".")[0]])
    for r in RELATIONS:
        if r["left"] in fields and r["right"] in fields:
            out.add(f"relation:{r['left']}={r['right']}")
    return out
