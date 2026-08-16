from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from frontend.product.db import Base
from frontend.product.models import (
    AnswerClaim,
    Citation,
    Conversation,
    Document,
    KnowledgeBase,
    Message,
    User,
)
from frontend.product.services.knowledge_health import (
    assemble_health_payload,
    compute_data_health,
    compute_retrieval_health,
    compute_trust_health,
    create_health_snapshot,
    latest_health_snapshot,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as store:
        yield store
    engine.dispose()


def make_user(session: Session) -> User:
    user = User(
        username="owner",
        display_name="Owner",
        password_hash="x" * 60,
        role="admin",
    )
    session.add(user)
    session.commit()
    return user


def make_knowledge_base(session: Session, user: User) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(
        owner_id=user.id,
        name="测试知识库",
        description="",
        collection_name="test_coll",
        is_current=True,
    )
    session.add(knowledge_base)
    session.commit()
    return knowledge_base


def make_document(
    session: Session,
    knowledge_base: KnowledgeBase,
    *,
    status: str = "ready",
    page_count: int = 3,
    published_at: date | None = date(2026, 1, 1),
) -> Document:
    document = Document(
        knowledge_base_id=knowledge_base.id,
        display_name=f"{status}-{page_count}.pdf",
        storage_path="x.pdf",
        size_bytes=1,
        page_count=page_count,
        sha256=("a" + status + "b" * 62)[:64],
        status=status,
        published_at=published_at,
    )
    session.add(document)
    session.commit()
    return document


def make_conversation(
    session: Session,
    user: User,
    knowledge_base: KnowledgeBase,
) -> Conversation:
    conversation = Conversation(
        owner_id=user.id,
        knowledge_base_id=knowledge_base.id,
        title="对话",
    )
    session.add(conversation)
    session.commit()
    return conversation


def make_message(
    session: Session,
    conversation: Conversation,
    *,
    answer_state: str = "fully_grounded",
    policy_action: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="回答",
        status="completed",
        answer_state=answer_state,
        policy_action=policy_action,
    )
    session.add(message)
    session.commit()
    return message


def make_citation(session: Session, message: Message) -> Citation:
    citation = Citation(
        message_id=message.id,
        index=0,
        display_name="x.pdf",
        source_type="knowledge_base",
        trusted=True,
        text="证据",
        supported=True,
    )
    session.add(citation)
    session.commit()
    return citation


def make_claim(
    session: Session,
    message: Message,
    *,
    support_status: str = "supported",
    citation_status: str = "valid",
    consistency_status: str = "consistent",
    entailment_status: str = "entailed",
) -> AnswerClaim:
    claim = AnswerClaim(
        message_id=message.id,
        index=0,
        text="声明",
        support_status=support_status,
        structural_support_status=support_status,
        citation_status=citation_status,
        consistency_status=consistency_status,
        entailment_status=entailment_status,
        citation_indices=[0],
    )
    session.add(claim)
    session.commit()
    return claim


# ---- data health ----


def test_data_health_empty_knowledge_base():
    result = compute_data_health([], index_verified=False)
    assert result["score"] == 0.0
    assert result["metrics"]["total_documents"] == 0
    codes = {item["code"] for item in result["deductions"]}
    assert "KB_EMPTY" in codes
    assert any(item["code"] == "UPLOAD_DOCUMENTS" for item in result["actions"])


def test_data_health_full_ready():
    documents = [
        Document(
            knowledge_base_id="kb",
            display_name=f"d{i}.pdf",
            storage_path="x.pdf",
            size_bytes=1,
            page_count=2,
            sha256=f"{i:064d}",
            status="ready",
            published_at=date(2026, 1, 1),
        )
        for i in range(3)
    ]
    result = compute_data_health(documents, index_verified=True)
    assert result["score"] == 100.0
    assert result["metrics"]["ready_documents"] == 3
    assert result["deductions"] == []


def test_data_health_failures_and_missing_index():
    documents = [
        Document(
            knowledge_base_id="kb",
            display_name="ready.pdf",
            storage_path="x.pdf",
            size_bytes=1,
            page_count=2,
            sha256="0" * 64,
            status="ready",
        ),
        Document(
            knowledge_base_id="kb",
            display_name="failed.pdf",
            storage_path="x.pdf",
            size_bytes=1,
            page_count=0,
            sha256="1" * 64,
            status="failed",
        ),
    ]
    result = compute_data_health(documents, index_verified=False)
    assert result["score"] == pytest.approx(42.5)
    codes = {item["code"] for item in result["deductions"]}
    assert "FAILED_DOCUMENTS" in codes
    assert "INDEX_MISSING" in codes
    assert "TEMPORAL_METADATA_SPARSE" in codes


# ---- retrieval health ----


def test_retrieval_health_no_samples():
    result = compute_retrieval_health([])
    assert result["score"] is None
    assert result["metrics"]["message_sample_count"] == 0
    codes = {item["code"] for item in result["deductions"]}
    assert "NO_ANSWER_SAMPLES" in codes


def test_retrieval_health_below_min_sample():
    rows = [
        {
            "citation_count": 1,
            "web_citation_count": 0,
            "policy_action": None,
            "answer_state": "fully_grounded",
        }
        for _ in range(2)
    ]
    result = compute_retrieval_health(rows)
    assert result["score"] is None
    assert result["metrics"]["message_sample_count"] == 2


def test_retrieval_health_complete():
    rows = [
        {
            "citation_count": 2,
            "web_citation_count": 0,
            "policy_action": None,
            "answer_state": "fully_grounded",
        }
        for _ in range(4)
    ]
    result = compute_retrieval_health(rows)
    assert result["score"] == 100.0
    assert result["metrics"]["citation_coverage_rate"] == 1.0


def test_retrieval_health_refusal_penalty():
    rows = [
        {
            "citation_count": 1,
            "web_citation_count": 0,
            "policy_action": "refuse",
            "answer_state": "insufficient_evidence",
        }
        for _ in range(4)
    ]
    result = compute_retrieval_health(rows)
    assert result["score"] is not None
    assert result["score"] < 100.0
    codes = {item["code"] for item in result["deductions"]}
    assert "HIGH_REFUSAL_RATE" in codes
    assert "HIGH_INSUFFICIENT_EVIDENCE" in codes


# ---- trust health ----


def test_trust_health_no_claims():
    result = compute_trust_health([])
    assert result["score"] is None
    assert result["metrics"]["claim_count"] == 0
    codes = {item["code"] for item in result["deductions"]}
    assert "NO_CLAIMS" in codes


def test_trust_health_complete():
    rows = [
        {
            "support_status": "supported",
            "citation_status": "valid",
            "consistency_status": "consistent",
            "entailment_status": "entailed",
        }
        for _ in range(4)
    ]
    result = compute_trust_health(rows)
    assert result["score"] == 100.0


def test_trust_health_penalties():
    rows = [
        {
            "support_status": "unsupported",
            "citation_status": "missing",
            "consistency_status": "inconsistent",
            "entailment_status": "contradicted",
        },
        {
            "support_status": "supported",
            "citation_status": "valid",
            "consistency_status": "consistent",
            "entailment_status": "entailed",
        },
        {
            "support_status": "supported",
            "citation_status": "valid",
            "consistency_status": "consistent",
            "entailment_status": "entailed",
        },
        {
            "support_status": "conflicting",
            "citation_status": "conflicting",
            "consistency_status": "consistent",
            "entailment_status": "unknown",
        },
    ]
    result = compute_trust_health(rows)
    assert result["score"] is not None
    assert result["score"] < 100.0
    codes = {item["code"] for item in result["deductions"]}
    assert "INVALID_CITATIONS" in codes


# ---- assemble + snapshot persistence ----


def test_assemble_and_create_snapshot(session):
    user = make_user(session)
    knowledge_base = make_knowledge_base(session, user)
    knowledge_base.index_manifest = "{}"
    session.commit()
    make_document(session, knowledge_base)
    conversation = make_conversation(session, user, knowledge_base)
    for index in range(4):
        message = make_message(session, conversation)
        make_citation(session, message)
        make_claim(
            session,
            message,
            support_status="supported",
            citation_status="valid",
            consistency_status="consistent",
            entailment_status="entailed",
        )

    payload = assemble_health_payload(session, knowledge_base)
    assert payload["status"] == "complete"
    assert payload["data_score"] is not None
    assert payload["retrieval_score"] is not None
    assert payload["trust_score"] is not None
    assert payload["overall_score"] is not None
    assert payload["formula_version"] == "1.0"
    assert payload["deductions"] == []
    assert payload["actions"] == []

    snapshot, previous, change = create_health_snapshot(
        session,
        knowledge_base,
        user.id,
    )
    assert snapshot.id.startswith("khs_")
    assert previous is None
    assert change["direction"] == "new"
    assert latest_health_snapshot(session, knowledge_base.id).id == snapshot.id

    second, previous_second, change_second = create_health_snapshot(
        session,
        knowledge_base,
        user.id,
    )
    assert previous_second.id == snapshot.id
    assert change_second["direction"] == "changed"
    assert change_second["overall_delta"] == 0.0


def test_assemble_partial_when_no_samples(session):
    user = make_user(session)
    knowledge_base = make_knowledge_base(session, user)
    make_document(session, knowledge_base)

    payload = assemble_health_payload(session, knowledge_base)
    assert payload["status"] == "partial"
    assert payload["data_score"] is not None
    assert payload["retrieval_score"] is None
    assert payload["trust_score"] is None
    assert payload["overall_score"] is None
    codes = {item["code"] for item in payload["deductions"]}
    assert "NO_ANSWER_SAMPLES" in codes
    assert "NO_CLAIMS" in codes
