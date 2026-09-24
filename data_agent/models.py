from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from datetime import date


class Contract(BaseModel):
    """已确认的查询口径：指标、时间、维度和过滤条件。"""

    model_config = ConfigDict(extra="forbid")
    metric: Literal[
        "registrations", "activations", "activation_rate", "retention_d7", "cpa"
    ]
    start: date
    end: date
    dimensions: list[Literal["date", "channel", "os"]] = Field(
        default_factory=lambda: ["channel"], max_length=3
    )
    channel: str | None = None
    category: Literal["信息流", "搜索", "自然"] | None = None
    metric_version: Literal["v1"] = "v1"

    @model_validator(mode="after")
    def validate_range(self):
        if self.start > self.end:
            raise ValueError("开始日期不能晚于结束日期")
        if (self.end - self.start).days > 366:
            raise ValueError("单次查询最多 367 天")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("维度不能重复")
        if self.metric == "cpa" and "os" in self.dimensions:
            raise ValueError("花费数据没有操作系统维度，不能直接分摊 CPA")
        return self


class Plan(BaseModel):
    """查询计划，支持取数、分析或先向用户澄清。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["query", "analyze", "clarify"]
    contract: Contract | None = None
    analysis: Literal["table", "bar", "line", "summary"] = "table"
    clarification: str | None = None

    @model_validator(mode="after")
    def validate_plan(self):
        if self.action in ("query", "analyze") and self.contract is None:
            raise ValueError("查询或分析需要明确查询契约")
        if self.action == "clarify" and not self.clarification:
            raise ValueError("澄清需要提供具体问题")
        return self


class Ask(BaseModel):
    """新问题请求；memory_ids 仅包含用户主动选中的长期记忆。"""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    request_id: str = Field(min_length=1, max_length=100)

    memory_ids: list[str] = Field(default_factory=list, max_length=8)


class SaveMemory(BaseModel):
    """保存长期记忆的请求；source_id 指向字段、表或结果集。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["field", "table", "result"]
    source_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=2000)


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=100)
