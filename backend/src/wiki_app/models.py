"""首版持久化模型：原件、衍生结果、执行状态和历史各自保存。"""

from datetime import datetime, timezone
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from wiki_app.db import Base


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(Text)
    # URL 仅为兼容展示；内容身份移到 resources，同一内容允许多次主动收藏。
    url_key: Mapped[str | None] = mapped_column(String(64), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    purpose: Mapped[str] = mapped_column(String(20), default="auto")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # 平台下载的衍生媒体用于定位播放，不再次当作新上传来源识别。
    derived: Mapped[bool] = mapped_column(Boolean, default=False)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("record_id", "version", "origin", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    input_id: Mapped[str | None] = mapped_column(ForeignKey("capture_inputs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    method: Mapped[str] = mapped_column(String(50))
    search_text: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024).with_variant(JSON(), "sqlite"), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(200))


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # 阶段只存非敏感修订 ID；自动重试不能悄悄切换模型。
    model_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), unique=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSON)
    topic_evidence: Mapped[list] = mapped_column(JSON, default=list)
    points: Mapped[list] = mapped_column(JSON)
    observations: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = (UniqueConstraint("record_id", "target_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    feedback: Mapped[str] = mapped_column(String(20), default="suggested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WikiPage(Base):
    __tablename__ = "wiki_pages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    title_key: Mapped[str] = mapped_column(String(200), unique=True)
    generated: Mapped[str] = mapped_column(Text, default="")
    personal: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=0)
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WikiRevision(Base):
    __tablename__ = "wiki_revisions"
    __table_args__ = (UniqueConstraint("page_id", "version"), UniqueConstraint("page_id", "run_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey("wiki_pages.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("processing_runs.id"))
    version: Mapped[int] = mapped_column(Integer)
    generated: Mapped[str] = mapped_column(Text)
    personal: Mapped[str] = mapped_column(Text)
    source_ids: Mapped[list] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(20))
    change_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_call_cache", "run_id", "request_hash", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("processing_runs.id"), index=True)
    connection_id: Mapped[str | None] = mapped_column(String(36))
    revision_id: Mapped[str | None] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(30), default="llm")
    stage: Mapped[str] = mapped_column(String(50))
    request_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="reserved")
    charged_tokens: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict | None] = mapped_column(JSON)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ModelConnection(Base):
    """连接与凭据绑定；更换目标增加 endpoint_version，使旧任务停止而非泄露密钥。"""

    __tablename__ = "model_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    protocol: Mapped[str] = mapped_column(String(30))
    base_url: Mapped[str] = mapped_column(String(500))
    key_cipher: Mapped[str] = mapped_column(Text, default="")
    endpoint_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ManagedModel(Base):
    __tablename__ = "managed_models"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("model_connections.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    revision_id: Mapped[str] = mapped_column(String(36))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelRevision(Base):
    """不可变调用参数，不保存明文或加密凭据；轮换密钥不改变向量空间身份。"""

    __tablename__ = "model_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(ForeignKey("managed_models.id"), index=True)
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelPreferences(Base):
    __tablename__ = "model_preferences"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="personal")
    bindings: Mapped[dict] = mapped_column(JSON, default=dict)
    active_space_id: Mapped[str | None] = mapped_column(String(36))


class ModelProbe(Base):
    __tablename__ = "model_probes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("model_revisions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    succeeded: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmbeddingSpace(Base):
    __tablename__ = "embedding_spaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("model_revisions.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    activate_when_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SourceVector(Base):
    __tablename__ = "source_vectors"
    __table_args__ = (UniqueConstraint("space_id", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    space_id: Mapped[str] = mapped_column(ForeignKey("embedding_spaces.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024).with_variant(JSON(), "sqlite"))


class Resource(Base):
    """内容身份不等于收藏事件；只用确定性身份归并，不按相似标题合并。"""

    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CaptureInput(Base):
    """一次收藏某版本的输入来源；失败状态和读取片段一起持久化。"""

    __tablename__ = "capture_inputs"
    __table_args__ = (UniqueConstraint("record_id", "version", "origin"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    origin: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(50))
    details: Mapped[list] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(300), unique=True)


class RecordTopic(Base):
    """scope=record 为分析主题；scope=input_id 需要有可核对的来源支持。"""

    __tablename__ = "record_topics"
    __table_args__ = (UniqueConstraint("record_id", "version", "topic_id", "scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"), index=True)
    scope: Mapped[str] = mapped_column(String(36), default="record")
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    feedback: Mapped[str] = mapped_column(String(20), default="suggested")


class CaptureInsight(Base):
    """可选解释独立排队，失败不改变收藏的正常整理状态。"""

    __tablename__ = "capture_insights"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), unique=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    feedback: Mapped[str] = mapped_column(String(20), default="suggested")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewPreference(Base):
    """个人自托管实例只有一份偏好；它是用户数据，不修改部署 YAML。"""

    __tablename__ = "review_preferences"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="personal")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceReviewState(Base):
    __tablename__ = "resource_review_states"
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewBatch(Base):
    __tablename__ = "review_batches"
    __table_args__ = (UniqueConstraint("local_date", "timezone"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    local_date: Mapped[str] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewCard(Base):
    __tablename__ = "review_cards"
    __table_args__ = (UniqueConstraint("batch_id", "resource_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("review_batches.id"), index=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"))
    kind: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    feedback: Mapped[str] = mapped_column(String(20), default="pending")
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
