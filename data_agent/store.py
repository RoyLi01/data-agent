import sqlite3
import json
import uuid
import time
from contextlib import contextmanager
from .catalog import SCHEMA_VERSION


def uid():
    return uuid.uuid4().hex


class Conflict(ValueError):
    pass


class Store:
    """用 SQLite 保存会话、任务版本、幂等请求和结果集。"""

    def __init__(self, root):
        self.path = root / "state.sqlite"
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, owner TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,owner TEXT NOT NULL,request_id TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,request_id));
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,owner TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS resume_requests(owner TEXT NOT NULL,request_id TEXT NOT NULL,task_id TEXT NOT NULL,answer TEXT NOT NULL,PRIMARY KEY(owner,request_id));""")

    @contextmanager
    def connect(self):
        """为一次数据库操作提供事务，异常时回滚并关闭连接。"""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, question, session_id, request_id, owner, memory_ids=None):
        """创建任务并检查幂等键，同一会话只允许一个未完成任务。"""
        memory_ids = list(dict.fromkeys(memory_ids or []))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM tasks WHERE owner=? AND request_id=?",
                (owner, request_id),
            ).fetchone()
            if old:
                payload = json.loads(old["payload"])
                if (
                    payload["question"] != question
                    or payload.get("memory_ids", []) != memory_ids
                    or (session_id and old["session_id"] != session_id)
                ):
                    raise Conflict("幂等键已用于不同请求")
                return self._task(old), False
            if session_id:
                if not db.execute(
                    "SELECT 1 FROM sessions WHERE id=? AND owner=?", (session_id, owner)
                ).fetchone():
                    raise KeyError("会话不存在")
                if db.execute(
                    "SELECT 1 FROM tasks WHERE session_id=? AND state NOT IN ('COMPLETED','FAILED')",
                    (session_id,),
                ).fetchone():
                    raise Conflict("当前会话已有未完成任务，请先处理澄清或等待完成")
            else:
                session_id = uid()
                db.execute("INSERT INTO sessions VALUES (?,?)", (session_id, owner))
            task = {
                "id": uid(),
                "session_id": session_id,
                "owner": owner,
                "request_id": request_id,
                "state": "RECEIVED",
                "version": 0,
                "question": question,
                "memory_ids": memory_ids,
                "trace": [],
            }
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                (
                    task["id"],
                    session_id,
                    owner,
                    request_id,
                    task["state"],
                    0,
                    json.dumps(task, ensure_ascii=False),
                ),
            )
            return task, True

    def _task(self, row):
        t = json.loads(row["payload"])
        t.update(state=row["state"], version=row["version"])
        return t

    def get(self, task_id, owner):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, owner)
            ).fetchone()
        if not row:
            raise KeyError("任务不存在")
        return self._task(row)

    def save(self, t, state, **extra):
        """按允许的状态迁移更新任务；版本不匹配时拒绝覆盖。"""
        transitions = {
            "RECEIVED": {"PLANNING"},
            "PLANNING": {"NEEDS_CLARIFICATION", "RETRIEVING"},
            "NEEDS_CLARIFICATION": {"RECEIVED"},
            "RETRIEVING": {"VALIDATING", "ANALYZING"},
            "VALIDATING": {"EXECUTING"},
            "EXECUTING": {"ANALYZING"},
            "ANALYZING": {"COMPLETED"},
            "COMPLETED": set(),
            "FAILED": set(),
        }
        if (
            state != t["state"]
            and state != "FAILED"
            and state not in transitions.get(t["state"], set())
        ):
            raise Conflict("不允许的任务状态迁移")
        old = t["version"]
        updated = {**t, **extra, "state": state, "version": old + 1}
        with self.connect() as db:
            cur = db.execute(
                "UPDATE tasks SET state=?,version=?,payload=? WHERE id=? AND version=?",
                (state, old + 1, json.dumps(updated, ensure_ascii=False), t["id"], old),
            )
            if cur.rowcount != 1:
                raise Conflict("任务版本已改变")
        t.clear()
        t.update(updated)

    def claim_resume(self, task_id, owner, answer, version, request_id):
        """在同一事务内校验恢复请求、任务版本和澄清状态。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM resume_requests WHERE owner=? AND request_id=?",
                (owner, request_id),
            ).fetchone()
            row = db.execute(
                "SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, owner)
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            task = self._task(row)
            if old:
                if old["task_id"] != task_id or old["answer"] != answer:
                    raise Conflict("恢复幂等键冲突")
                return task, False
            if task["version"] != version or task["state"] != "NEEDS_CLARIFICATION":
                raise Conflict("任务版本或状态不匹配")
            db.execute(
                "INSERT INTO resume_requests VALUES (?,?,?,?)",
                (owner, request_id, task_id, answer),
            )
            task.update(state="RECEIVED", version=version + 1)
            task["question"] += "\n用户补充：" + answer
            db.execute(
                "UPDATE tasks SET state=?,version=?,payload=? WHERE id=?",
                (
                    "RECEIVED",
                    version + 1,
                    json.dumps(task, ensure_ascii=False),
                    task_id,
                ),
            )
            return task, True

    def artifact(self, result, session_id, owner):
        """保存结果集及其来源，设置有效期，供后续分析复用。"""
        a = {
            **result,
            "id": uid(),
            "owner": owner,
            "session_id": session_id,
            "created": time.time(),
            "expires": time.time() + 3600,
        }
        with self.connect() as db:
            db.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?)",
                (
                    a["id"],
                    session_id,
                    owner,
                    a["created"],
                    json.dumps(a, ensure_ascii=False),
                ),
            )
        return a

    def latest(self, session_id, owner):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM artifacts WHERE session_id=? AND owner=? ORDER BY created DESC LIMIT 1",
                (session_id, owner),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_artifact(self, artifact_id, owner):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM artifacts WHERE id=? AND owner=?",
                (artifact_id, owner),
            ).fetchone()
        if not row:
            raise KeyError("结果不存在")
        return json.loads(row[0])


def reuse(artifact, contract, watermark, owner):
    """检查历史结果是否足以回答当前契约，返回可复用标记和原因。

    除契约匹配外，还检查所有者、有效期、完整性、Schema 和数据版本。
    仅允许已有完整渠道结果的安全筛选，不随意重新聚合比率或去重指标。"""
    if not artifact:
        return False, "没有历史结果"
    if artifact["owner"] != owner:
        return False, "权限不匹配"
    if artifact["expires"] < time.time():
        return False, "历史结果已过期"
    if artifact["is_truncated"]:
        return False, "历史结果不完整"
    if (
        artifact["schema_version"] != SCHEMA_VERSION
        or artifact["snapshot"] != watermark["snapshot"]
        or artifact["watermark"] != watermark["complete_through"]
    ):
        return False, "数据或元数据版本改变"
    old = dict(artifact["contract"])
    new = contract.model_dump(mode="json")
    if old == new:
        return True, "查询契约一致，直接复用完整结果"
    # A dimension already present may be filtered; no unsafe aggregate rollup.
    if (
        old.get("channel") is None
        and new.get("channel")
        and "channel" in old["dimensions"]
    ):
        old["channel"] = new["channel"]
        if old == new:
            return True, "在完整渠道维度结果上筛选"
    return False, "指标、日期、维度或筛选范围改变，需要重新取数"
