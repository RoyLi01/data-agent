import os,sys,json,asyncio
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from .config import ROOT
from .query import Warehouse,QueryError

class Gateway:
    def __init__(self,settings):self.settings=settings
    async def query(self,contract):
        if self.settings.transport=='local':return Warehouse(self.settings).query(contract)
        if self.settings.transport!='stdio':raise ValueError('传输模式必须为 local 或 stdio')
        # Child receives data location, not model keys. Server enforces the same contract independently.
        env={'PATH':os.environ.get('PATH',''),'PYTHONPATH':str(ROOT),'AGENT_DATA_DIR':str(self.settings.data_dir),'AGENT_LOAD_DOTENV':'0'}
        params=StdioServerParameters(command=sys.executable,args=['-m','data_agent.mcp_server'],env=env,cwd=str(ROOT))
        async with asyncio.timeout(20):
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    available=await session.list_tools()
                    if 'query_metric' not in {t.name for t in available.tools}:raise ValueError('MCP 服务未暴露 query_metric')
                    reply=await session.call_tool('query_metric',{'contract':contract})
                    if reply.isError:raise QueryError('MCP_ERROR','MCP 查询调用失败')
                    payload=reply.structuredContent
                    if payload is None:payload=json.loads(next(x.text for x in reply.content if x.type=='text'))
        if not payload['ok']:raise QueryError(payload['error']['code'],payload['error']['message'])
        return payload['result']
