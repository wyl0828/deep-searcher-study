from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from frontend.product.db import Base

LEGACY_OWNER_ID = "usr_legacy_owner"


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


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("usr"))
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(back_populates="owner")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="owner")
    owned_workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("name", name="uq_workspaces_name"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("wsp"))
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    owner: Mapped[User] = relationship(back_populates="owned_workspaces")
    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(back_populates="workspace")


class WorkspaceMember(TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_members_workspace_user",
        ),
        CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')",
            name="ck_workspace_members_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("wsm"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="workspace_memberships")


class KnowledgeBaseMember(TimestampMixin, Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "user_id",
            name="uq_kb_members_kb_user",
        ),
        CheckConstraint(
            "role IN ('editor', 'viewer')",
            name="ck_kb_members_role",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
            name="fk_kb_members_workspace_user",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "knowledge_base_id"],
            ["knowledge_bases.workspace_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_kb_members_workspace_kb",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("kbm"))
    knowledge_base_id: Mapped[str] = mapped_column(String(40), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class MemberGroup(TimestampMixin, Base):
    __tablename__ = "member_groups"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_member_groups_workspace_name",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_member_groups_workspace_id",
        ),
        CheckConstraint(
            "role IN ('editor', 'viewer')",
            name="ck_member_groups_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("grp"))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class GroupMember(TimestampMixin, Base):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_group_members_group_user",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
            name="fk_group_members_workspace_user",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["member_groups.workspace_id", "member_groups.id"],
            ondelete="CASCADE",
            name="fk_group_members_workspace_group",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("grm"))
    group_id: Mapped[str] = mapped_column(String(40), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[str] = mapped_column(String(40), nullable=False)

    group: Mapped[MemberGroup] = relationship(back_populates="members")


class UserSession(TimestampMixin, Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("ses"))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_knowledge_bases_workspace_name"),
        UniqueConstraint("workspace_id", "id", name="uq_knowledge_bases_workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("kb"))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        default=LEGACY_OWNER_ID,
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    collection_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    index_manifest: Mapped[str | None] = mapped_column(Text)
    index_previous_collection: Mapped[str | None] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner: Mapped[User] = relationship(back_populates="knowledge_bases")
    workspace: Mapped[Workspace] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    health_snapshots: Mapped[list["KnowledgeHealthSnapshot"]] = relationship(
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
    storage_type: Mapped[str] = mapped_column(String(16), default="local", nullable=False)
    storage_bucket: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[date | None] = mapped_column(Date)
    effective_at: Mapped[date | None] = mapped_column(Date)
    superseded_at: Mapped[date | None] = mapped_column(Date)
    temporal_metadata_source: Mapped[str | None] = mapped_column(String(32))
    version_family: Mapped[str | None] = mapped_column(String(128), index=True)
    version_family_source: Mapped[str | None] = mapped_column(String(32))

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
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(80), default="新对话", nullable=False)

    owner: Mapped[User] = relationship(back_populates="conversations")
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    summaries: Mapped[list["ConversationSummary"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationSummary.created_at",
    )


class ConversationSummary(TimestampMixin, Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("sum"))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    last_message_id: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="summaries")


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
    trust_contract_version: Mapped[int | None] = mapped_column(Integer)
    trust_status: Mapped[str | None] = mapped_column(String(32))
    safety_status: Mapped[str | None] = mapped_column(String(24))
    policy_action: Mapped[str | None] = mapped_column(String(24))
    policy_profile: Mapped[str | None] = mapped_column(String(32))
    policy_reason_codes: Mapped[list[str] | None] = mapped_column(JSON)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    query_type: Mapped[str | None] = mapped_column(String(32))
    risk_factors: Mapped[list[str] | None] = mapped_column(JSON)
    provenance_contract_version: Mapped[int | None] = mapped_column(Integer)
    provenance_digest: Mapped[str | None] = mapped_column(String(71), index=True)
    trust_details: Mapped[dict | None] = mapped_column(JSON)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="Citation.index",
    )
    claims: Mapped[list["AnswerClaim"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="AnswerClaim.index",
    )


@event.listens_for(Conversation, "before_insert")
def inherit_conversation_owner(_mapper, connection, conversation: Conversation) -> None:
    if conversation.owner_id:
        return
    knowledge_base = conversation.knowledge_base
    owner_id = knowledge_base.owner_id if knowledge_base is not None else None
    if owner_id is None and conversation.knowledge_base_id:
        owner_id = connection.execute(
            select(KnowledgeBase.owner_id).where(KnowledgeBase.id == conversation.knowledge_base_id)
        ).scalar_one_or_none()
    conversation.owner_id = owner_id or LEGACY_OWNER_ID


class AnswerClaim(TimestampMixin, Base):
    __tablename__ = "answer_claims"
    __table_args__ = (
        UniqueConstraint("message_id", "index", name="uq_answer_claims_message_index"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("claim"))
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    support_status: Mapped[str] = mapped_column(String(32), nullable=False)
    structural_support_status: Mapped[str] = mapped_column(
        String(32), default="unsupported", nullable=False
    )
    citation_indices: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    citation_spans: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    citation_status: Mapped[str] = mapped_column(String(24), default="missing", nullable=False)
    entailment_status: Mapped[str] = mapped_column(
        String(24), default="not_checked", nullable=False
    )
    consistency_status: Mapped[str] = mapped_column(
        String(24), default="not_checked", nullable=False
    )
    consistency_checks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    risk_status: Mapped[str] = mapped_column(String(24), default="not_assessed", nullable=False)
    risk_checks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    message: Mapped[Message] = relationship(back_populates="claims")


class Citation(TimestampMixin, Base):
    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(
        String(48), primary_key=True, default=lambda: make_id("citation")
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
    published_at: Mapped[date | None] = mapped_column(Date)
    effective_at: Mapped[date | None] = mapped_column(Date)
    superseded_at: Mapped[date | None] = mapped_column(Date)
    temporal_metadata_source: Mapped[str | None] = mapped_column(String(32))
    version_family: Mapped[str | None] = mapped_column(String(128))
    version_family_source: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    message: Mapped[Message] = relationship(back_populates="citations")


class KnowledgeHealthSnapshot(TimestampMixin, Base):
    """Versioned knowledge health snapshot for one knowledge base.

    The snapshot persists the formula version, the per-dimension scores, the raw
    metrics that produced them, the deduction reasons and the suggested actions so
    a health score can be audited instead of being a black-box number.
    """

    __tablename__ = "knowledge_health_snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("khs"))
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    formula_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="complete", nullable=False)
    overall_score: Mapped[float | None] = mapped_column(Float)
    data_score: Mapped[float | None] = mapped_column(Float)
    retrieval_score: Mapped[float | None] = mapped_column(Float)
    trust_score: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict | None] = mapped_column(JSON)
    deductions: Mapped[list[dict] | None] = mapped_column(JSON)
    actions: Mapped[list[dict] | None] = mapped_column(JSON)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="health_snapshots")


class OperationAuditLog(Base):
    """System-level operation audit (P1-4.1, aligned with ragent BizChangeLogDO).

    Immutable append-only log: who changed what permission/config, with the
    before/after JSON snapshots and a path-based diff so a change can be
    reconstructed without the original request payloads.
    """

    __tablename__ = "operation_audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: make_id("aud"))
    biz_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    biz_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action_desc: Mapped[str] = mapped_column(String(512), nullable=False)
    before_snapshot: Mapped[dict | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict | None] = mapped_column(JSON)
    change_diff: Mapped[list[dict] | None] = mapped_column(JSON)
    operator_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    operator_name: Mapped[str | None] = mapped_column(String(128))
    operator_role: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
