from mcp.server.fastmcp import FastMCP
from .config import Settings
from .models import Contract
from .query import Warehouse,QueryError

mcp=FastMCP('Data Agent read-only warehouse',log_level='ERROR')

@mcp.tool()
def query_metric(contract:dict,sql:str|None=None)->dict:
    """Execute a registered metric contract. Read-only, schema-bound, budgeted by caller. No identity parameter."""
    try:return {'ok':True,'result':Warehouse(Settings.env()).query(Contract.model_validate(contract).model_dump(mode='json'),sql)}
    except (ValueError,QueryError) as exc:return {'ok':False,'error':{'code':getattr(exc,'code','INVALID_CONTRACT'),'message':str(exc)}}

if __name__=='__main__':mcp.run(transport='stdio')
