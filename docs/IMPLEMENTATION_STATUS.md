# 实施状态与续作检查点

更新：2026-09-24。目录 `/Users/ruoyangli/Desktop/大模型/Data Agent/`；旧 RAG 项目只读。本轮分支 `codex/retrieval-memory`。

## 本轮新增

1. 真实本地中文 Embedding + BM25 双路召回、RRF、CrossEncoder 重排。模型已下载并实际运行，缓存不入 Git。
2. 字段三级表示：keyword/semantic/rerank；结构化知识分块、Schema 关联子图、连接路径和 Join 字段补全。
3. 上下文聚合：短期窗口、摘要、历史结果及显式长期记忆；离线可运行，live Tool Calling 接口已接入。执行端按契约和数据状态动态选择查询、分析、澄清。
4. 长短期记忆：最近 6 轮、持久化异步摘要队列、递增游标、启动恢复；主动保存字段/表/结果、搜索、选择、删除、跨会话使用。
5. 前端记忆操作入口，聚合请求和路由可视化检查。已有结果跨会话使用仍保留原 SQL 证据。

## 验证

- 原有 SQL、状态机、MCP、技能、报告、澄清功能保留。
- 53 项自动化测试；包括模型适配桩测试，不等于真实 LLM 调用。
- 30 查询、8 多轮、2 澄清开发回归通过，SQL 结果由独立 Python 计算对照。
- 真实本地模型检索 6 条样本目标全部命中 Top 5；见 `evals/latest_local_retrieval.json`。仅为小样本开发验证，不作独立准确率或生产性能声明。
- 浏览器：本地语义检索+实际 MCP 查询、收藏结果、新建会话选用已收藏结果绘图。
- 本地 `.env` 已配置 local 检索和重排；规划模式仍为 offline，不含密钥。

## 可读性整理（2026-09-24）

- 已展开检索、Schema、上下文、记忆、引擎、存储、API、模型结构和计划器共 9 个 Python 文件，以及前端 HTML/CSS/JavaScript。
- 补充中文函数说明和关键流程注释，解释 RRF、关联路径、结果有效性与后台摘要。
- 去除文档字符串并统一导入表示后，9 个 Python 文件的执行语法树与整理前一致；前端脚本语法校验通过，53 项原有测试通过。
- 未新增业务逻辑。复现测试命令仍为 `.venv/bin/python -m pytest -q`；后续待办维持下方联调清单。

## 尚待联调或未实现

- 真实 LLM 聚合、计划、展示决策及摘要：需自行在 `.env` 配置 `MODEL_BASE_URL/MODEL_NAME/MODEL_API_KEY` 并设 `AGENT_MODE=live`。
- 远程 Embedding/Reranker、Milvus：有适配，未连接真实服务。
- Docker：已更新 CPU 模型依赖与配置；尚未构建/运行，需 Docker daemon。
- 任意自由 SQL 生成和修复、任意 Python 沙箱、复杂自动探索、因果归因、ClickHouse/PostgreSQL、企业认证/行列权限均不在当前完成范围。
- 当前为单进程本地演示；多副本摘要队列需要租约或独立消息系统。离线解析的语义覆盖有限，不是泛化模型能力。

## 复现

```bash
cd '/Users/ruoyangli/Desktop/大模型/Data Agent'
.venv/bin/python -m pytest -q
.venv/bin/python evals/run.py
.venv/bin/python evals/retrieval_local.py
./scripts/start.sh
```

设计细节见 `docs/RETRIEVAL_AND_MEMORY.md`；密钥、运行数据、模型权重不提交。

## 额度约定

保留至少 10% 的 5 小时共享额度；剩余约 15% 开始收尾，接近 10% 保存并暂停。额度是账户共享百分比，不能保证其他任务不会同时消耗。
