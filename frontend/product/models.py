from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from frontend.product.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("kb"))
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    collection_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    index_manifest: Mapped[str | None] = mapped_column(Text)
    index_previous_collection: Mapped[str | None] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "sha256",
            name="uq_documents_knowledge_base_sha256",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("doc"))
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    jobs: Mapped[list["IngestJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class IngestJob(TimestampMixin, Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("job"))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="jobs")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("conv"))
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(80), default="新对话", nullable=False)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("msg"))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    answer_state: Mapped[str | None] = mapped_column(String(32))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="Citation.index",
    )


class Citation(TimestampMixin, Base):
    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: make_id("citation")
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(255))
    section_path: Mapped[list[str] | None] = mapped_column(JSON)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)
    location_id: Mapped[str | None] = mapped_column(String(64))
    source_locator: Mapped[str | None] = mapped_column(String(128))
    parser_version: Mapped[str | None] = mapped_column(String(128))
    extraction_method: Mapped[str | None] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(
        String(32),
        default="knowledge_base",
        nullable=False,
    )
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_domain: Mapped[str | None] = mapped_column(String(253))
    trusted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    message: Mapped[Message] = relationship(back_populates="citations")
