"""统一查询/元数据 MCP 入口；只传数据配置，不向工具进程传模型密钥。"""

import os
import sys
import json
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from .config import ROOT
from .query import Warehouse, QueryError


class Gateway:
    def __init__(self, settings):
        self.settings = settings

    async def query(self, contract):
        return await self.call("query_metric", {"contract": contract})

    async def call(self, name, arguments):
        if self.settings.transport == "local":
            if name == "query_metric":
                return Warehouse(self.settings).query(**arguments)
            if name == "query_sql":
                from .sql_agent import execute_general

                return execute_general(self.settings, **arguments)
            if name == "find_tables_by_fields":
                from .metadata_search import search
                from .warehouse_build import read_profiles

                return search(
                    **arguments, profiles=read_profiles(self.settings.data_dir)
                )
            if name == "trace_lineage":
                from .lineage import trace_lineage

                return trace_lineage(**arguments)
            raise ValueError("未知工具")
        if self.settings.transport != "stdio":
            raise ValueError("传输模式必须为 local 或 stdio")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT),
            "AGENT_DATA_DIR": str(self.settings.data_dir),
            "AGENT_LOAD_DOTENV": "0",
            "AGENT_MAX_ROWS": str(self.settings.max_rows),
            "AGENT_QUERY_TIMEOUT": str(self.settings.query_timeout),
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "data_agent.mcp_server"],
            env=env,
            cwd=str(ROOT),
        )
        async with asyncio.timeout(max(20, self.settings.query_timeout + 5)):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    available = await session.list_tools()
                    if name not in {t.name for t in available.tools}:
                        raise ValueError("MCP 服务未暴露工具")
                    reply = await session.call_tool(name, arguments)
                    if reply.isError:
                        raise QueryError("MCP_ERROR", "MCP 工具调用失败")
                    payload = reply.structuredContent
                    if payload is None:
                        payload = json.loads(
                            next(x.text for x in reply.content if x.type == "text")
                        )
        if not payload["ok"]:
            raise QueryError(payload["error"]["code"], payload["error"]["message"])
        return payload["result"]
