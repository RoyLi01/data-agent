# Data Agent · 企业数仓问数与分析

一个独立的、可在本地运行的问数项目。已实现：指标注册、结构化元数据检索、受控 SQL、MCP 调用、任务状态机、澄清恢复、多轮结果复用、统计图表、证据报告和回归评测。原 RAG 项目仅作参考，没有修改或复制凭据。

**当前是可运行的业务首版，尚未完成原技术方案的所有生产组件。** 默认使用离线规则解析器和词法哈希向量，明确标记为测试替身，不是真实模型或语义 Embedding。详细状态见 [实施状态](docs/IMPLEMENTATION_STATUS.md)。

## 立即运行

本机虚拟环境及依赖已经安装：

```bash
cd '/Users/ruoyangli/Desktop/大模型/Data Agent'
./scripts/start.sh
```

打开 <http://127.0.0.1:8000>。API 文档在 `/docs`。首次运行自动生成模拟数据；以后复用，不覆盖已有数据。默认通过实际 MCP stdio 客户端/服务端执行查询。

其他机器首次安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
./scripts/start.sh
```

需要 Python 3.11+，SQLite 3.39+（CPA 模板使用 FULL OUTER JOIN）。程序启动时会检查 SQLite 版本。

## 演示顺序

1. `上个月各渠道的成功激活人数` → 得到去重后的人数和来源 SQL。
2. `画柱状图` → 复用已有结果。
3. `再按操作系统拆开` → 发现缺少维度，重新取数。
4. `画趋势图` → 缺少日期维度时补查。
5. 新建会话，输入 `上个月各渠道的新增用户数` → 澄清注册还是激活；输入 `注册人数` 后继续。
6. 新建会话，输入 `2026-09-01 至 2026-09-20 的 D7 留存率` → 明确提示排除尚未成熟的注册队列。

离线演示将当前日期固定为 **2026-09-22**，模拟数据完整范围为 **2026-06-01 至 2026-09-20**。因此“上个月”指 2026 年 8 月，不随机器日期变化。无日期的新问题也使用上个完整自然月，最终契约明确展示日期。

## 业务规则

- 注册：注册自然日内的用户数；一用户一条注册事实。
- 激活：发生日内 `status=success` 的去重用户；原始数据包含失败及重复事件。
- 注册七日激活率：注册当天至第 6 日成功激活的 cohort 用户 / 同 cohort 注册用户。
- D7 留存：注册后第 7 自然日活跃用户 / 同 cohort 注册用户，仅纳入已成熟队列。
- CPA：同一查询范围的花费 / 成功激活去重人数；两事实先各自聚合，避免关联放大。不支持将渠道花费按操作系统任意分摊。

中文同义词和明确日期只是离线演示覆盖范围，不代表任意自然语言理解。未支持的同比/环比请求需要澄清，不静默解释为普通取数。核心指标使用注册表编译模板，不执行任意模型 SQL；校验不是任意 SQL 业务等价性的证明。

## 配置真实模型

复制 `.env.example` 为 `.env`（请勿将真实密钥提交 Git），本地填写：

```dotenv
AGENT_MODE=live
MODEL_BASE_URL=https://your-provider.example/compatible-mode/v1
MODEL_NAME=your-tool-calling-model
MODEL_API_KEY=your-key
```

接口需支持 `/chat/completions` 和原生 `tools`、`tool_choice`。当前代码有查询计划及分析角色的 Tool Calling 适配；尚未使用真实凭据联调。模型只选择登记指标、参数和分析类型，SQL 计算与结果统计由程序完成。

语义向量检索另外配置 `EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_API_KEY`。接口需支持 `/embeddings`。重排接口配置 `RERANK_URL/MODEL/API_KEY`，输入 `{model,query,documents,top_n}`，返回 `results[].index`；供应商协议不同则需增加适配。没有配置时显示 `offline_lexical_hash` 和 `offline_token_overlap`，不冒充真实语义检索和模型重排。

可选 Milvus 后端代码位于 `retrieval.py`，安装 `pip install 'pymilvus>=2.5,<3'` 并设置 `MILVUS_URI`、`MILVUS_TOKEN` 后使用；当前未联调。不配置时使用内存向量索引，BM25 在应用层计算。模型版本改变需要新建索引，索引名由 Schema 与模型配置派生。

## 测试与评测

```bash
.venv/bin/python -m pytest -q
.venv/bin/python evals/run.py
```

已验证的离线回归：42 项自动化测试；另有 30 个查询场景、8 个多轮场景、2 个澄清场景。参考结果来自独立 Python 计算，未调用生产 SQL 编译器。报告保存到 `evals/latest_offline.json`。

这些是开发回归场景，不是独立盲测集。输出中 `llm_accuracy=null`；不能将全部通过写成“模型准确率 100%”。依赖补全消融只验证注册表补全规则的覆盖效果。真实模型基线、模型消融、Token 成本和实际端到端延迟须接入服务后另外测试。

## Docker

```bash
docker compose up --build -d
```

仅把服务发布到宿主机 `127.0.0.1:8000`；数据位于专用卷 `agent-data`，不接触旧 RAG 的容器或数据库。镜像以非 root 用户运行。Docker 配置已提供，但当前 Docker daemon 未启动，尚未验证镜像构建与容器运行。本地 Python 已验证可用。

## 工程目录

```text
data_agent/
  catalog.py      指标、表字段、关联元数据与结构化分块
  retrieval.py    BM25、向量、RRF、重排及依赖补全
  planner.py      离线解析器 / 原生 Tool Calling 适配
  engine.py       状态编排、查询和分析角色、预算
  query.py        指标编译、SQLGlot 校验、只读执行
  gateway.py      MCP 客户端
  mcp_server.py   MCP 只读查询工具
  store.py        会话、任务版本、幂等恢复、结果复用
  analysis.py     固定统计、SVG 绘图、Markdown 报告
  api.py          HTTP 接口及本地单用户访问边界
skills/           渐进式查询、分析规范与工具白名单
static/           浏览器工作台
runtime/          本地数据和任务状态，不入版本库
tests/            自动化测试
evals/            独立参考计算、开发评测场景及报告
```

本版是本地单用户演示。可配置 `AGENT_ACCESS_TOKEN` 限制访问，但不是企业多租户认证；服务端身份固定为 `local-demo`，不信任模型提供用户 ID。结果读取和复用检查所有者，复用另检查版本、时效、粒度和完整性。生产行列级权限、ClickHouse/PostgreSQL 后端和隔离 Python Worker 尚未实现。
