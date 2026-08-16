from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from frontend.product.db import Base
from frontend.product.errors import ProductError
from frontend.product.models import (
    AnswerClaim,
    Citation,
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgeHealthSnapshot,
    Message,
    User,
)
from frontend.product.services.knowledge_health import (
    assemble_health_payload,
    compute_retrieval_attribution,
    compute_series_detections,
    compute_data_health,
    compute_retrieval_health,
    compute_trust_health,
    create_health_snapshot,
    get_health_level,
    health_trend_payload,
    latest_health_snapshot,
    run_health_actions,
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
    assert payload["formula_version"] == "1.1"
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


# ---- series detections (formula 1.1) ----


def make_series_document(
    *,
    family: str,
    sha256: str,
    start: date,
    end: date | None = None,
    display_name: str = "doc.pdf",
    status: str = "ready",
) -> Document:
    return Document(
        knowledge_base_id="kb",
        display_name=display_name,
        storage_path="x.pdf",
        size_bytes=1,
        page_count=2,
        sha256=sha256,
        status=status,
        version_family=family,
        published_at=start,
        effective_at=start,
        superseded_at=end,
    )


def test_series_duplicate_group_single_deduction():
    documents = [
        make_series_document(family="handbook", sha256="a" * 64, start=date(2026, 1, 1), display_name="a.pdf"),
        make_series_document(family="handbook", sha256="a" * 64, start=date(2026, 1, 2), display_name="b.pdf"),
        make_series_document(family="handbook", sha256="a" * 64, start=date(2026, 1, 3), display_name="c.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    codes = [item["code"] for item in deductions]
    assert codes.count("DUPLICATE_DOCUMENTS") == 1
    assert penalty == 10
    result = compute_data_health(documents, index_verified=True)
    assert result["metrics"]["series_penalty"] == 10
    assert result["score"] == 90.0


def test_series_equal_end_start_is_continuity():
    documents = [
        make_series_document(family="handbook", sha256="1" * 64, start=date(2026, 1, 1), end=date(2026, 6, 1), display_name="v1.pdf"),
        make_series_document(family="handbook", sha256="2" * 64, start=date(2026, 6, 1), display_name="v2.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    assert deductions == []
    assert penalty == 0
    result = compute_data_health(documents, index_verified=True)
    assert result["score"] == 100.0


def test_series_gap_threshold_90_and_91():
    def chain(gap_days: int):
        v1_start = date(2026, 1, 1)
        v1_end = v1_start + timedelta(days=30)
        v2_start = v1_end + timedelta(days=gap_days)
        return [
            make_series_document(family="policy", sha256="1" * 64, start=v1_start, end=v1_end, display_name="v1.pdf"),
            make_series_document(family="policy", sha256="2" * 64, start=v2_start, display_name="v2.pdf"),
        ]

    deductions_90, _, penalty_90 = compute_series_detections(chain(90))
    assert deductions_90 == []
    assert penalty_90 == 0
    deductions_91, _, penalty_91 = compute_series_detections(chain(91))
    codes_91 = [item["code"] for item in deductions_91]
    assert codes_91 == ["SERIES_GAP"]
    assert penalty_91 == 5


def test_series_future_effective_not_multiple_current():
    today = date.today()
    documents = [
        make_series_document(family="roadmap", sha256="1" * 64, start=today - timedelta(days=10), display_name="current.pdf"),
        make_series_document(family="roadmap", sha256="2" * 64, start=today + timedelta(days=90), display_name="future.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    codes = [item["code"] for item in deductions]
    assert "SERIES_SUPERSEDE_ANOMALY" not in codes
    assert penalty == 0


def test_series_invalid_interval_excluded_from_downstream():
    documents = [
        make_series_document(family="handbook", sha256="1" * 64, start=date(2026, 6, 1), end=date(2026, 1, 1), display_name="bad.pdf"),
        make_series_document(family="handbook", sha256="2" * 64, start=date(2026, 3, 1), display_name="ok.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    codes = [item["code"] for item in deductions]
    assert "SERIES_SUPERSEDE_ANOMALY" in codes
    assert "TEMPORAL_OVERLAP" not in codes
    assert "SERIES_GAP" not in codes
    assert penalty == 10


def test_series_local_suppression_duplicate_and_gap_coexist():
    documents = [
        make_series_document(family="mix", sha256="a" * 64, start=date(2026, 1, 1), end=date(2026, 6, 1), display_name="a1.pdf"),
        make_series_document(family="mix", sha256="a" * 64, start=date(2026, 2, 1), end=date(2026, 6, 1), display_name="a2.pdf"),
        make_series_document(family="mix", sha256="b" * 64, start=date(2026, 6, 1), end=date(2026, 6, 2), display_name="b.pdf"),
        make_series_document(family="mix", sha256="c" * 64, start=date(2026, 6, 2) + timedelta(days=120), display_name="c.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    codes = [item["code"] for item in deductions]
    assert codes.count("DUPLICATE_DOCUMENTS") == 1
    assert codes.count("SERIES_GAP") == 1
    assert penalty == 15


def test_series_overlap_adjacent_only():
    documents = [
        make_series_document(family="handbook", sha256="1" * 64, start=date(2026, 1, 1), end=date(2026, 3, 15), display_name="a.pdf"),
        make_series_document(family="handbook", sha256="2" * 64, start=date(2026, 3, 1), end=date(2026, 3, 20), display_name="b.pdf"),
        make_series_document(family="handbook", sha256="3" * 64, start=date(2026, 3, 20), end=date(2026, 4, 1), display_name="c.pdf"),
        make_series_document(family="handbook", sha256="4" * 64, start=date(2026, 4, 1), display_name="d.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    codes = [item["code"] for item in deductions]
    assert codes.count("TEMPORAL_OVERLAP") == 1
    assert penalty == 10


def test_series_orphan_no_successor():
    documents = [
        make_series_document(family="handbook", sha256="1" * 64, start=date(2026, 1, 1), end=date(2026, 6, 1), display_name="v1.pdf"),
    ]
    deductions, _, penalty = compute_series_detections(documents)
    codes = [item["code"] for item in deductions]
    assert codes == ["SERIES_ORPHANED"]
    assert penalty == 5


def test_series_penalty_cap_at_40():
    documents = []
    for i in range(5):
        family = f"fam{i}"
        start = date(2026, 1, 1) + timedelta(days=i)
        for j in range(2):
            documents.append(
                make_series_document(
                    family=family,
                    sha256="a" * 64,
                    start=start,
                    display_name=f"{family}-{j}.pdf",
                )
            )
    result = compute_data_health(documents, index_verified=True)
    assert result["metrics"]["series_penalty"] == 40
    assert result["score"] == 60.0


# ---- retrieval attribution ----


def test_retrieval_attribution_unknown_query_type():
    rows = [
        {"citation_count": 0, "web_citation_count": 0, "policy_action": "refuse", "answer_state": "insufficient_evidence", "query_type": None},
        {"citation_count": 1, "web_citation_count": 0, "policy_action": None, "answer_state": "fully_grounded", "query_type": "comparison"},
    ]
    attribution = compute_retrieval_attribution(rows)
    refusal = {item["query_type"]: item for item in attribution["refusal_by_query_type"]}
    assert refusal["unknown"]["sample_count"] == 1
    assert refusal["unknown"]["count"] == 1
    assert refusal["unknown"]["rate"] == 1.0
    assert refusal["comparison"]["count"] == 0


def test_retrieval_attribution_counts_and_rates():
    rows = [
        {"query_type": "comparison", "policy_action": "refuse", "answer_state": "insufficient_evidence", "citation_count": 0, "web_citation_count": 0},
        {"query_type": "comparison", "policy_action": None, "answer_state": "fully_grounded", "citation_count": 1, "web_citation_count": 0},
        {"query_type": "comparison", "policy_action": None, "answer_state": "fully_grounded", "citation_count": 1, "web_citation_count": 0},
    ]
    attribution = compute_retrieval_attribution(rows)
    comparison = attribution["refusal_by_query_type"][0]
    assert comparison["sample_count"] == 3
    assert comparison["count"] == 1
    assert comparison["rate"] == pytest.approx(0.3333, abs=0.001)


def test_retrieval_attribution_unreferenced_denominator():
    ready = [
        Document(id="d1", knowledge_base_id="kb", display_name="used.pdf", storage_path="x", size_bytes=1, page_count=1, sha256="1" * 64, status="ready"),
        Document(id="d2", knowledge_base_id="kb", display_name="unused.pdf", storage_path="x", size_bytes=1, page_count=1, sha256="2" * 64, status="ready"),
        Document(id="d3", knowledge_base_id="kb", display_name="failed.pdf", storage_path="x", size_bytes=1, page_count=0, sha256="3" * 64, status="failed"),
    ]
    attribution = compute_retrieval_attribution([], ready, {"d1"})
    assert attribution["referenced_ready_coverage"] == pytest.approx(0.5)
    names = [item["display_name"] for item in attribution["unreferenced_documents"]]
    assert names == ["unused.pdf"]


# ---- health level ----


def test_health_level_boundaries():
    assert get_health_level(39) == "critical"
    assert get_health_level(40) == "warning"
    assert get_health_level(59) == "warning"
    assert get_health_level(60) == "healthy"
    assert get_health_level(None) is None


# ---- trend + actions ----


def test_health_trend_payload_ascending_with_level(session):
    user = make_user(session)
    knowledge_base = make_knowledge_base(session, user)
    snapshots = [
        KnowledgeHealthSnapshot(knowledge_base_id=knowledge_base.id, owner_id=user.id, formula_version="1.1", status="complete", overall_score=50.0, data_score=60.0, retrieval_score=40.0, trust_score=50.0),
        KnowledgeHealthSnapshot(knowledge_base_id=knowledge_base.id, owner_id=user.id, formula_version="1.1", status="complete", overall_score=80.0, data_score=90.0, retrieval_score=70.0, trust_score=80.0),
    ]
    session.add_all(snapshots)
    session.flush()
    payload = health_trend_payload(snapshots)
    assert [item["overall"] for item in payload["items"]] == [50.0, 80.0]
    assert payload["items"][0]["level"] == "warning"
    assert payload["items"][1]["level"] == "healthy"


def test_run_health_actions_upload(session):
    user = make_user(session)
    knowledge_base = make_knowledge_base(session, user)
    result = asyncio.run(
        run_health_actions(session, knowledge_base, user.id, ["UPLOAD_DOCUMENTS"])
    )
    assert result["results"][0]["status"] == "requires_user_action"
    assert result["snapshot"]["formula_version"] == "1.1"
    assert result["delta"]["direction"] == "new"


def test_run_health_actions_unknown_raises(session):
    user = make_user(session)
    knowledge_base = make_knowledge_base(session, user)
    with pytest.raises(ProductError):
        asyncio.run(
            run_health_actions(session, knowledge_base, user.id, ["NOT_A_REAL_ACTION"])
        )


def test_run_health_actions_retry_uses_service(session, monkeypatch):
    user = make_user(session)
    knowledge_base = make_knowledge_base(session, user)
    make_document(session, knowledge_base, status="failed")
    calls: list[str] = []

    def fake_retry(session, document):
        calls.append(document.id)

    monkeypatch.setattr(
        "frontend.product.services.knowledge_health.retry_document",
        fake_retry,
    )
    result = asyncio.run(
        run_health_actions(
            session,
            knowledge_base,
            user.id,
            ["RETRY_FAILED_DOCUMENTS"],
        )
    )
    assert len(calls) == 1
    assert result["results"][0]["status"] == "succeeded"
    assert result["results"][0]["affected_count"] == 1
