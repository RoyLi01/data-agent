"""目录检索：精确筛选不经过 Top K，语义搜索明确返回候选。"""

import base64
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .catalog import TABLES, SCHEMA_VERSION
from .metadata_loader import digest


class MetadataSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_terms: list[str] = Field(default_factory=list, max_length=10)
    match_mode: Literal["exact", "contains", "semantic"] = "exact"
    field_operator: Literal["all", "any"] = "all"
    layer: str | None = None
    domain: str | None = None
    page_size: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


def parse_search(question, previous=None):
    """有限中文规则覆盖物理字段查表；其他语义通过别名/检索发现候选。"""
    follow = previous and bool(re.search(r"只看|还要|再加|改成|下一页", question))
    data = dict(previous) if follow else {}
    data.pop("cursor", None)
    layers = re.findall(r"\b(ODS|DWD|DWS|ADS|DIM|SOURCE)\b", question, re.I)
    if layers:
        data["layer"] = layers[-1].upper()
    fields = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*\b", question)
    fields = [
        f
        for f in fields
        if f.upper() not in {"ODS", "DWD", "DWS", "ADS", "DIM", "SOURCE", "AND", "OR"}
    ]
    fields = list(dict.fromkeys(fields))
    if fields:
        if follow and re.search("还要|再加", question):
            fields = list(dict.fromkeys(data.get("field_terms", []) + fields))
        data["field_terms"] = fields
        data["match_mode"] = (
            "contains"
            if re.search("字段名.*(?:带有|包含)|模糊|子串", question)
            else "exact"
        )
    elif not follow:
        semantic_terms = [
            word
            for word in (
                "用户标识",
                "用户ID",
                "渠道ID",
                "操作系统",
                "注册日期",
                "内容ID",
            )
            if word in question
        ]
        data["field_terms"] = semantic_terms or ([question] if not layers else [])
        data["match_mode"] = "semantic" if data["field_terms"] else "exact"
    if re.search(r"或者|任一|\bor\b|或", question, re.I):
        data["field_operator"] = "any"
    if re.search("同时|还要|再加", question):
        data["field_operator"] = "all"
    for word, domain in [
        ("增长", "growth"),
        ("投放", "advertising"),
        ("内容", "content"),
    ]:
        if word in question:
            data["domain"] = domain
    return MetadataSearchRequest.model_validate(data)


def search(request, retriever=None, allowed_tables=None, profiles=None):
    r = (
        MetadataSearchRequest.model_validate(request)
        if isinstance(request, dict)
        else request
    )
    allowed = (
        set(TABLES) if allowed_tables is None else set(allowed_tables) & set(TABLES)
    )
    if r.layer and r.layer not in {"ODS", "DWD", "DWS", "ADS", "DIM", "SOURCE"}:
        raise ValueError("未知目录层级")
    semantic = {}
    if r.match_mode == "semantic":
        for term in r.field_terms:
            matches = {}
            for name in sorted(allowed):
                for col, field in TABLES[name]["fields"].items():
                    labels = [col, field["description"], *field.get("aliases", [])]
                    if any(label and label in term for label in labels):
                        matches[(name, col)] = "业务别名或字段含义"
            if retriever:
                hits = retriever.search(term, k=20, allowed_tables=sorted(allowed))[
                    "chunks"
                ]
                for hit in hits:
                    if hit["id"].startswith("field:"):
                        name, col = hit["id"][6:].split(".")
                        matches.setdefault((name, col), "混合检索候选，需确认含义")
            semantic[term] = matches
    rows = []
    for name in sorted(allowed):
        table = TABLES[name]
        if r.layer and table["layer"] != r.layer:
            continue
        if r.domain and table["domain"] != r.domain:
            continue
        term_matches = []
        evidence = {}
        for term in r.field_terms:
            matches = []
            for col in table["columns"]:
                found = (
                    col == term
                    if r.match_mode == "exact"
                    else term in col
                    if r.match_mode == "contains"
                    else (name, col) in semantic[term]
                )
                if found:
                    matches.append(col)
                    evidence[col] = (
                        semantic[term][(name, col)]
                        if r.match_mode == "semantic"
                        else r.match_mode
                    )
            term_matches.append(bool(matches))
        matches_all = (
            all(term_matches) if r.field_operator == "all" else any(term_matches)
        )
        if r.field_terms and not matches_all:
            continue
        rows.append(
            {
                "table": name,
                "layer": table["layer"],
                "domain": table["domain"],
                "grain": table["grain"],
                "description": table["description"],
                "matched_fields": [
                    {"name": c, "description": table["columns"][c], "reason": reason}
                    for c, reason in sorted(evidence.items())
                ],
            }
        )
    config = r.model_dump(exclude={"cursor"})
    signature = digest([config, sorted(allowed), SCHEMA_VERSION])
    offset = 0
    if r.cursor:
        try:
            token = json.loads(base64.urlsafe_b64decode(r.cursor))
            if token["signature"] != signature:
                raise ValueError()
            offset = token["offset"]
            if type(offset) is not int or not 0 <= offset <= len(rows):
                raise ValueError()
        except Exception as exc:
            raise ValueError("分页条件或目录已变化，请重新查询") from exc
    end = offset + r.page_size
    cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"signature": signature, "offset": end}).encode()
        ).decode()
        if end < len(rows)
        else None
    )
    return {
        "request": config,
        "rows": rows[offset:end],
        "total": len(rows),
        "next_cursor": cursor,
        "schema_version": SCHEMA_VERSION,
        "collected_at": (profiles or {}).get("collected_at"),
        "match_kind": "semantic_candidates"
        if r.match_mode == "semantic"
        else "exact_directory_filter",
        "scope": "当前登记目录；字段名大小写敏感。语义结果为候选，不承诺穷尽。",
    }
