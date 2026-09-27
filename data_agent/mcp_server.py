from mcp.server.fastmcp import FastMCP
from .config import Settings
from .models import Contract
from .query import Warehouse, QueryError

mcp = FastMCP("Data Agent read-only warehouse", log_level="ERROR")


@mcp.tool()
def query_metric(contract: dict, sql: str | None = None) -> dict:
    """Execute a registered metric contract. Read-only, schema-bound, budgeted by caller. No identity parameter."""
    try:
        return {
            "ok": True,
            "result": Warehouse(Settings.env()).query(
                Contract.model_validate(contract).model_dump(mode="json"), sql
            ),
        }
    except (ValueError, QueryError) as exc:
        return {
            "ok": False,
            "error": {
                "code": getattr(exc, "code", "INVALID_CONTRACT"),
                "message": str(exc),
            },
        }


@mcp.tool()
def query_sql(sql: str, spec: dict) -> dict:
    """执行登记目录内的通用只读查询，独立于原指标模板工具。"""
    from .sql_agent import execute_general

    try:
        return {"ok": True, "result": execute_general(Settings.env(), sql, spec)}
    except (ValueError, QueryError) as exc:
        return {
            "ok": False,
            "error": {
                "code": getattr(exc, "code", "INVALID_REQUEST"),
                "message": str(exc),
            },
        }


@mcp.tool()
def find_tables_by_fields(request: dict) -> dict:
    """目录精确/子串匹配，返回稳定分页；语义候选由编排层检索。"""
    from .metadata_search import search
    from .warehouse_build import read_profiles

    try:
        return {
            "ok": True,
            "result": search(request, profiles=read_profiles(Settings.env().data_dir)),
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error": {"code": "INVALID_METADATA_REQUEST", "message": str(exc)},
        }


@mcp.tool()
def trace_lineage(table: str) -> dict:
    """返回登记加工 SQL 和上游来源，不将 Join 边作为加工边。"""
    from .lineage import trace_lineage as trace

    try:
        return {"ok": True, "result": trace(table)}
    except ValueError as exc:
        return {"ok": False, "error": {"code": "INVALID_LINEAGE", "message": str(exc)}}


if __name__ == "__main__":
    mcp.run(transport="stdio")
