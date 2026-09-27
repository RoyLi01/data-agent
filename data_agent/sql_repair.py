"""修复循环只负责错误反馈与预算，不修改原始 QuerySpec。"""

import copy
import sqlglot
from .query import QueryError

REPAIRABLE = {"SQL_SYNTAX", "SCHEMA_BINDING", "SQL_EXECUTION", "CONTRACT_MISMATCH"}


async def execute_with_repair(
    spec, initial_sql, execute, propose, max_repairs=2, trace=None
):
    events = trace if trace is not None else []
    locked = spec.model_copy(deep=True)
    original = locked.model_dump()
    sql = initial_sql
    seen = set()
    for attempt in range(max_repairs + 1):
        try:
            result = await execute(sql, locked.model_copy(deep=True))
            events.append({"attempt": attempt + 1, "sql": sql, "status": "succeeded"})
            return result, events
        except QueryError as exc:
            events.append(
                {
                    "attempt": attempt + 1,
                    "sql": sql,
                    "status": "failed",
                    "code": exc.code,
                    "message": str(exc),
                }
            )
            try:
                canonical = sqlglot.parse_one(sql, read="sqlite").sql(
                    normalize=True, comments=False
                )
            except Exception:
                canonical = " ".join(sql.split())
            key = (canonical, exc.code)
            if key in seen or exc.code not in REPAIRABLE or attempt == max_repairs:
                raise
            seen.add(key)
            feedback = {
                "original_request": original,
                "previous_sql": sql,
                "code": exc.code,
                "message": str(exc),
                "repair_attempt": attempt + 1,
            }
            sql = await propose(locked.model_copy(deep=True), copy.deepcopy(feedback))
    raise RuntimeError("不可达修复状态")
