import asyncio
import time
import copy
from .config import Settings
from .seed import seed
from .store import Store
from .retrieval import Retriever
from .skills import SkillRegistry, Budget
from .planner import Planner, ModelError
from .gateway import Gateway
from .query import Warehouse, QueryError
from .analysis import analyze
from .memory import Memory
from .context import ContextAggregator, route
from .models import Plan


class Engine:
    """串联上下文、检索、计划、查询和分析的任务执行入口。"""

    def __init__(self, settings=None):
        self.settings = settings or Settings.env()
        seed(self.settings.data_dir)
        self.store = Store(self.settings.data_dir)
        self.retriever = Retriever()
        self.skills = SkillRegistry()
        self.planner = Planner(self.settings.mode, self.settings.as_of)
        self.gateway = Gateway(self.settings)
        self.memory = Memory(
            self.store, self.planner.model if self.settings.mode == "live" else None
        )
        self.aggregator = ContextAggregator(self.planner)
        self.retrieval_lock = asyncio.Lock()

    async def ask(
        self, question, session_id, request_id, owner="local-demo", memory_ids=None
    ):
        """创建幂等任务；相同请求重复提交时返回原任务。"""
        self.memory.selected(owner, memory_ids or [])
        t, fresh = self.store.create(
            question, session_id, request_id, owner, memory_ids
        )
        if fresh:
            await self.run(t)
        return t

    async def resume(self, task_id, answer, version, request_id, owner="local-demo"):
        """根据任务版本恢复澄清流程，防止重复或过期回答被执行。"""
        t, fresh = self.store.claim_resume(task_id, owner, answer, version, request_id)
        if fresh:
            await self.run(t)
        return t

    def step(self, t, state, note, **data):
        """记录流程轨迹并持久化状态，每次写入都检查任务版本。"""
        t["trace"].append({"state": state, "note": note, "time": time.time()})
        self.store.save(t, state, **data)

    async def retrieve(self, question, contract=None):
        """将检索放到后台线程，并串行保护同一实例的向量初始化。"""
        async with self.retrieval_lock:
            return await asyncio.to_thread(self.retriever.search, question, contract)

    async def run(self, t):
        """执行单个任务。t 是可持久化的任务字典，c 是确认后的查询契约。

        依次聚合上下文、检索 Schema、生成计划、选择复用或取数、生成报告。
        finally 中保存耗时与工具记录，并把本轮加入短期记忆。"""
        started = time.monotonic()
        budget = Budget(self.settings.max_calls)
        try:
            query_skill = self.skills.load("metric_query")
            self.step(
                t,
                "PLANNING",
                "聚合近期对话、异步摘要及主动选中的长期记忆",
                mode=self.settings.mode,
                skills=self.skills.list(),
            )
            # 1. 读取用户选中的长期记忆与近期窗口，确定本轮完整请求。
            memories = self.memory.selected(t["owner"], t.get("memory_ids", []))
            short = self.memory.snapshot(t["session_id"], t["owner"])
            candidates = self.memory.artifacts(t["session_id"], t["owner"], memories)
            aggregate, offline_plan, context_info = await asyncio.to_thread(
                self.aggregator.aggregate, t["question"], short, candidates, memories
            )
            previous = next(
                (a for a in candidates if a["id"] == aggregate.artifact_id), None
            )
            t["context"] = {
                "aggregate": aggregate.model_dump(),
                "short_memory": short,
                "selected_memory_ids": [m["id"] for m in memories],
                "stale_memory_ids": [
                    m["id"] for m in memories if not m["valid_schema"]
                ],
                "model": context_info,
            }
            budget.consume(query_skill, "search_metadata")
            hints = " ".join(
                m["source_id"] + " " + m["note"][:200]
                for m in memories
                if m["valid_schema"] and m["kind"] != "result"
            )
            # 2. 先检索 Schema，再生成指标契约；业务歧义会进入澄清状态。
            preliminary = await self.retrieve(aggregate.request + " " + hints)
            if aggregate.intent == "clarify":
                plan = Plan(action="clarify", clarification=aggregate.clarification)
                model_info = context_info
            elif offline_plan is not None:
                plan = offline_plan
                model_info = {"mode": "offline_fixture", "tokens": None}
            else:
                plan, model_info = await asyncio.to_thread(
                    self.planner.plan,
                    aggregate.request,
                    previous["contract"] if previous else None,
                    query_skill,
                    {
                        "chunks": preliminary["chunks"],
                        "schema_graph": preliminary["schema_graph"],
                    },
                )
            t["model"] = model_info
            t["plan"] = plan.model_dump(mode="json")
            if plan.action == "clarify":
                t["routing"] = {
                    "suggested": "clarify",
                    "effective": "clarify",
                    "reason": plan.clarification,
                }
                self.step(
                    t,
                    "NEEDS_CLARIFICATION",
                    plan.clarification,
                    clarification=plan.clarification,
                )
                return
            c = plan.contract
            if c.channel and c.channel not in ["渠道A", "渠道B", "渠道C", "自然流量"]:
                raise QueryError("UNKNOWN_CHANNEL", "渠道名称不在模拟数据的登记范围内")
            self.step(t, "RETRIEVING", "按已确认指标补全字段和关联依赖")
            budget.consume(query_skill, "search_metadata")
            context = await self.retrieve(aggregate.request + " " + hints, c)
            t["retrieval"] = context
            # 3. 查询条件和数据有效性共同决定是否复用，不能只听模型建议。
            routing, reusable = route(
                aggregate, previous, c, Warehouse(self.settings).watermark, t["owner"]
            )
            t["routing"] = routing
            reason = routing["reason"]
            t["reuse"] = {"reused": reusable, "reason": reason}
            # 4. 复用完整结果或通过受控工具取数，随后统一进入分析。
            if reusable:
                result = copy.deepcopy(previous)
                if c.channel and c.channel != previous["contract"].get("channel"):
                    result["rows"] = [
                        r for r in result["rows"] if r.get("channel") == c.channel
                    ]
                    result["contract"] = c.model_dump(mode="json")
                    result["row_count"] = len(result["rows"])
                    result["parent_artifact_id"] = previous["id"]
                    result["transform"] = {"filter_channel": c.channel}
                    result = self.store.artifact(result, t["session_id"], t["owner"])
                self.step(t, "ANALYZING", reason, artifact_id=result["id"])
            else:
                self.step(
                    t, "VALIDATING", "编译已登记指标模板，校验字段、访问范围和 SQL 结构"
                )
                # Preflight fails fast without sending an invalid contract to the MCP tool.
                from .query import compile_query, validate_sql

                sql, _ = compile_query(c, Warehouse(self.settings).watermark)
                validate_sql(sql, sql)
                self.step(t, "EXECUTING", reason)
                for attempt in range(2):
                    budget.consume(query_skill, "query_metric")
                    try:
                        result = await self.gateway.query(c.model_dump(mode="json"))
                        break
                    except (TimeoutError, ConnectionError):
                        if attempt:
                            raise
                        t["trace"].append(
                            {
                                "state": "RETRY",
                                "note": "查询服务暂不可用，重试一次",
                                "time": time.time(),
                            }
                        )
                result = self.store.artifact(result, t["session_id"], t["owner"])
                self.step(
                    t,
                    "ANALYZING",
                    "加载分析技能，对完整结果执行统计与可视化",
                    artifact_id=result["id"],
                )
            # 5. 分析工具负责实际计算，模型仅选择受支持的展示类型。
            analysis_skill = self.skills.load("result_analysis")
            kind = plan.analysis
            if self.settings.mode == "live":
                schema = {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["table", "summary", "bar", "line"],
                        }
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                }
                decision, usage = await asyncio.to_thread(
                    self.planner.model.call,
                    [
                        {
                            "role": "system",
                            "content": "你是分析角色，只选择展示工具类型，不自行计算数据。趋势图需要 date 列。"
                            + analysis_skill["instructions"],
                        },
                        {
                            "role": "user",
                            "content": str(
                                {
                                    "question": t["question"],
                                    "columns": result["columns"],
                                    "row_count": result["row_count"],
                                    "requested": kind,
                                }
                            ),
                        },
                    ],
                    "select_analysis",
                    schema,
                )
                if decision.get("kind") not in ("table", "summary", "bar", "line"):
                    raise ModelError("分析工具参数无效")
                kind = decision["kind"]
                t["analyst_usage"] = usage
            budget.consume(analysis_skill, "analyze_result")
            report = analyze(result, kind)
            if result.get("transform"):
                report["evidence"]["transform"] = result["transform"]
                report["evidence"]["parent_artifact_id"] = result["parent_artifact_id"]
                report["markdown"] += (
                    "\n结果变换："
                    + str(result["transform"])
                    + "\n父结果 ID："
                    + result["parent_artifact_id"]
                    + "\n"
                )
            self.step(
                t,
                "COMPLETED",
                "分析完成，可追溯至契约、SQL、结果和分析步骤",
                report=report,
                result={
                    "columns": result["columns"],
                    "rows": result["rows"][:200],
                    "total_rows": result["row_count"],
                    "preview_truncated": result["row_count"] > 200,
                    "is_truncated": result["is_truncated"],
                },
            )
        except Exception as exc:
            # No credentials, provider bodies or raw tracebacks in user-facing state.
            safe = (
                str(exc)
                if isinstance(
                    exc, (ValueError, QueryError, ModelError, PermissionError)
                )
                else "内部执行失败，请查看本地测试或联系维护者"
            )
            self.step(
                t,
                "FAILED",
                safe,
                error={
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": safe,
                },
            )
        finally:
            self.store.save(
                t,
                t["state"],
                elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                tool_calls=budget.events,
            )
            self.memory.record(t)
