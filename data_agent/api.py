from contextlib import asynccontextmanager
from pathlib import Path
import os,secrets,time
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.responses import FileResponse,PlainTextResponse,Response
from fastapi.staticfiles import StaticFiles
from .config import ROOT,Settings
from .engine import Engine
from .models import Ask,Resume
from .store import Conflict
from .catalog import METRICS,TABLES,SCHEMA_VERSION

@asynccontextmanager
async def lifespan(app):
    app.state.engine=Engine()
    # An interrupted process does not silently replay a partially completed query.
    store=app.state.engine.store
    with store.connect() as db:
        rows=db.execute("SELECT * FROM tasks WHERE state NOT IN ('COMPLETED','FAILED','NEEDS_CLARIFICATION')").fetchall()
    for row in rows:
        t=store._task(row);store.save(t,'FAILED',error={'code':'PROCESS_INTERRUPTED','message':'进程中断；请用新的请求重新执行。已保存的澄清任务仍可恢复。'})
    yield

app=FastAPI(title='Data Agent',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

def identity(request:Request,authorization:str|None=Header(default=None)):
    token=os.getenv('AGENT_ACCESS_TOKEN','')
    if token and not secrets.compare_digest(authorization or '', 'Bearer '+token):raise HTTPException(401,'访问令牌无效')
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):raise HTTPException(403,'不允许跨站请求')
    return 'local-demo' # Deliberate single-user demo, never accepts a user-id supplied by the model.

def engine(request:Request):return request.app.state.engine

@app.exception_handler(Conflict)
async def conflict(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409,content={'detail':str(exc)})

@app.exception_handler(KeyError)
async def missing(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=404,content={'detail':'请求的资源不存在'})

@app.get('/')
def home():return FileResponse(ROOT/'static/index.html')

@app.get('/api/health')
def health(e:Engine=Depends(engine)):
    return {'status':'ok','mode':e.settings.mode,'transport':e.settings.transport,'as_of':e.settings.as_of,
            'model_configured':bool(os.getenv('MODEL_API_KEY') and os.getenv('MODEL_NAME')),
            'embedding':'remote_semantic' if e.retriever.semantic else 'offline_lexical_hash','schema_version':SCHEMA_VERSION,'watermark':e.gateway.settings.data_dir.joinpath('watermark.json').exists()}

@app.get('/api/catalog')
def catalog(owner=Depends(identity)):return {'metrics':METRICS,'tables':TABLES}

@app.post('/api/ask')
async def ask(body:Ask,owner=Depends(identity),e:Engine=Depends(engine)):
    return await e.ask(body.question,body.session_id,body.request_id,owner)

@app.get('/api/tasks/{task_id}')
def task(task_id:str,owner=Depends(identity),e:Engine=Depends(engine)):return e.store.get(task_id,owner)

@app.post('/api/tasks/{task_id}/resume')
async def resume(task_id:str,body:Resume,owner=Depends(identity),e:Engine=Depends(engine)):
    return await e.resume(task_id,body.answer,body.expected_version,body.request_id,owner)

@app.get('/api/artifacts/{artifact_id}')
def artifact(artifact_id:str,owner=Depends(identity),e:Engine=Depends(engine)):
    a=e.store.get_artifact(artifact_id,owner)
    if a['expires']<time.time():raise HTTPException(410,'结果已过期，请重新查询')
    return a

@app.get('/api/tasks/{task_id}/report')
def report(task_id:str,owner=Depends(identity),e:Engine=Depends(engine)):
    t=e.store.get(task_id,owner)
    if t['state']!='COMPLETED':raise HTTPException(409,'报告尚未完成')
    return PlainTextResponse(t['report']['markdown'],headers={'Content-Disposition':f'attachment; filename="report-{task_id}.md"'})
