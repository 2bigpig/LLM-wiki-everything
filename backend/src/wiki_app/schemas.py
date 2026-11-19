"""接口与模型输出使用显式类型；模型输出中的引用仍需业务校验。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Purpose = Literal["auto", "learning", "interview", "note"]


class RecordCreate(BaseModel):
    title: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=200_000)
    url: str | None = Field(default=None, max_length=4096)
    notes: str = Field(default="", max_length=20_000)
    purpose: Purpose = "auto"

    @model_validator(mode="after")
    def require_content(self):
        if not self.content.strip() and not self.url:
            raise ValueError("请填写短文或链接")
        return self


class RecordEdit(BaseModel):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=200_000)
    notes: str = Field(max_length=20_000)
    purpose: Purpose

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()


class WikiEdit(BaseModel):
    version: int = Field(ge=0)
    personal: str = Field(max_length=100_000)


class WikiRestore(BaseModel):
    version: int = Field(ge=0)
    revision: int = Field(ge=1)


class RelationFeedback(BaseModel):
    feedback: Literal["accepted", "ignored", "suggested"]


class StrictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(StrictDraft):
    text: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(min_length=1, max_length=30)


class AnalysisDraft(StrictDraft):
    kind: Literal["summary", "learning", "interview", "note"]
    summary: str = Field(min_length=1, max_length=6000)
    topics: list[str] = Field(max_length=8)
    # 旧分析可不含映射；没有映射时只关联记录，不猜测哪个附件支持主题。
    topic_evidence: list["TopicEvidence"] = Field(default_factory=list, max_length=8)
    points: list[Point] = Field(min_length=1, max_length=30)
    observations: str = Field(default="", max_length=6000)

    @field_validator("topics")
    @classmethod
    def clean_topics(cls, topics):
        return list(dict.fromkeys(t.strip()[:100] for t in topics if t.strip()))


class RelationDraft(StrictDraft):
    target_id: str
    kind: Literal["same_topic", "complements", "extends", "contradicts", "experience", "duplicate"]
    explanation: str = Field(min_length=1, max_length=2000)
    source_id: str
    target_source_id: str
    source_quote: str = Field(min_length=4, max_length=1000)
    target_quote: str = Field(min_length=4, max_length=1000)


class RelationsDraft(StrictDraft):
    relations: list[RelationDraft] = Field(max_length=6)


class TopicEvidence(StrictDraft):
    topic: str = Field(min_length=1, max_length=100)
    source_ids: list[str] = Field(min_length=1, max_length=30)


AnalysisDraft.model_rebuild()


class InsightEvidence(StrictDraft):
    source_id: str
    quote: str = Field(min_length=4, max_length=1000)


class InsightDraft(StrictDraft):
    explanation: str = Field(default="", max_length=2000)
    evidence: list[InsightEvidence] = Field(default_factory=list, max_length=6)


class CapturePreview(BaseModel):
    text: str = Field(default="", max_length=200000)


class InsightFeedback(BaseModel):
    feedback: Literal["suggested", "accepted", "ignored"]


class ReviewFeedback(BaseModel):
    action: Literal["seen", "helpful", "snooze", "inaccurate", "never"]


class ReviewPreferenceEdit(BaseModel):
    enabled: bool | None = None
    pause: bool = False
    resume: bool = False


class WikiSection(StrictDraft):
    heading: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8000)
    source_ids: list[str] = Field(min_length=1, max_length=30)

    @field_validator("heading", "content")
    @classmethod
    def nonempty_section(cls, value):
        if not value.strip():
            raise ValueError("Wiki 章节不能为空")
        return value.strip()


class WikiDraft(StrictDraft):
    page_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    sections: list[WikiSection] = Field(min_length=1, max_length=8)

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        if not value.strip():
            raise ValueError("Wiki 标题不能为空")
        return value.strip()


class WikiDrafts(StrictDraft):
    pages: list[WikiDraft] = Field(max_length=3)
    reason: str = Field(default="", max_length=2000)
