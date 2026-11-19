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
    url_key: Mapped[str | None] = mapped_column(String(64), unique=True)
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


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("record_id", "version", "origin", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
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
    run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), index=True)
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
