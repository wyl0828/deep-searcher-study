import asyncio
import hashlib
import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from deepsearcher.provenance import build_trust_provenance
from deepsearcher.trust import build_temporal_context
from frontend.product.auth import require_user
from frontend.product.db import Base, create_database_engine, get_session
from frontend.product.errors import ProductError
from frontend.product.models import (
    Citation,
    Conversation,
    Document,
    KnowledgeBase,
    Message,
    User,
)
from frontend.product.repositories import get_conversation
from frontend.product.services import conversations as conversation_service
from frontend.product.services import documents as document_service
from frontend.product.services.conversations import (
    _finish_assistant_message,
    _iter_sse_events,
    _validate_stream_envelope,
    build_conversation_history,
    collect_supported_citations,
    query_error_from_response,
    stream_message_events,
)
from frontend.server import app


@pytest.fixture(autouse=True)
def stub_pdf_page_inspection(monkeypatch):
    async def inspect_pdf_pages(_path):
        return 1

    monkeypatch.setattr(
        "frontend.product.services.documents.inspect_pdf_pages",
        inspect_pdf_pages,
    )


@contextmanager
def product_client(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    with test_session() as session:
        test_user = User(
            username="test-user",
            display_name="测试用户",
            password_hash="disabled",
            role="admin",
        )
        session.add(test_user)
        session.commit()

    def override_session():
        with test_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[require_user] = lambda: test_user
    try:
        client = TestClient(app)
        client.product_session_factory = test_session
        yield client
    finally:
        app.dependency_overrides.clear()


def parse_sse_events(payload: str) -> list[dict]:
    events = []
    for frame in payload.replace("\r\n", "\n").split("\n\n"):
        data_lines = [line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events


def test_stream_envelope_requires_matching_request_id_and_contiguous_sequence():
    first = {
        "version": 1,
        "request_id": "request.with-dots-1",
        "sequence": 1,
        "event": "started",
        "data": {"stage": "query_started"},
    }

    assert _validate_stream_envelope(
        "started",
        first,
        expected_request_id="request.with-dots-1",
        last_sequence=0,
    ) == ("request.with-dots-1", 1)

    for invalid in (
        {**first, "request_id": "another-request"},
        {**first, "sequence": 2},
        {**first, "version": 2},
        {**first, "event": "routing"},
    ):
        with pytest.raises(ProductError) as exc_info:
            _validate_stream_envelope(
                "started",
                invalid,
                expected_request_id="request.with-dots-1",
                last_sequence=0,
            )
        assert exc_info.value.code == "QUERY_STREAM_INVALID"


def test_stream_parser_rejects_oversized_frames(monkeypatch):
    monkeypatch.setattr(conversation_service, "MAX_SSE_LINE_CHARS", 32)

    class Response:
        async def aiter_lines(self):
            yield "event: started"
            yield "data: " + ("x" * 33)
            yield ""

    async def consume():
        return [event async for event in _iter_sse_events(Response())]

    with pytest.raises(ProductError) as exc_info:
        asyncio.run(consume())
    assert exc_info.value.code == "QUERY_STREAM_INVALID"


def test_citations_never_bind_to_a_document_from_another_knowledge_base(tmp_path):
    with product_client(tmp_path) as client:
        own_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "当前知识库", "description": ""},
        ).json()
        foreign_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "其他知识库", "description": ""},
        ).json()
        conversation_payload = client.post(
            "/api/conversations",
            json={"knowledge_base_id": own_kb["id"]},
        ).json()

        with client.product_session_factory() as session:
            foreign_document = Document(
                knowledge_base_id=foreign_kb["id"],
                display_name="foreign-secret.pdf",
                storage_path=str(tmp_path / "foreign-secret.pdf"),
                size_bytes=10,
                sha256="a" * 64,
                status="ready",
            )
            assistant_message = Message(
                conversation_id=conversation_payload["id"],
                role="assistant",
                content="answer",
                status="pending",
            )
            session.add_all([foreign_document, assistant_message])
            session.commit()
            trace = {
                "iterations": [
                    {
                        "retrieved_documents": [
                            {
                                "document_id": foreign_document.id,
                                "display_name": "safe-fallback.pdf",
                                "text": "bounded evidence",
                                "supported": True,
                            }
                        ]
                    }
                ]
            }

            citations = collect_supported_citations(
                session,
                message=assistant_message,
                trace=trace,
            )

            assert len(citations) == 1
            assert citations[0].document_id is None
            assert citations[0].display_name == "safe-fallback.pdf"
            assert "foreign-secret" not in citations[0].display_name


def test_claim_grounding_persists_partial_support_and_rejects_fake_evidence_ids(tmp_path):
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "声明引用库", "description": ""},
        ).json()
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        with client.product_session_factory() as session:
            document = Document(
                knowledge_base_id=knowledge_base["id"],
                display_name="grounding.pdf",
                storage_path=str(tmp_path / "grounding.pdf"),
                size_bytes=10,
                sha256="e" * 64,
                status="ready",
            )
            assistant = Message(
                conversation_id=conversation["id"],
                role="assistant",
                content="",
                status="pending",
            )
            session.add_all([document, assistant])
            session.commit()
            _finish_assistant_message(
                session,
                assistant_message=assistant,
                payload={
                    "result": "Milvus 是向量数据库。[E1] 它支持任意 SQL。[E9]",
                    "trace": {
                        "grounding": {
                            "version": 1,
                            "state": "partially_grounded",
                            "evidence": [
                                {
                                    "evidence_id": "E1",
                                    "document_id": document.id,
                                    "display_name": "grounding.pdf",
                                    "page_number": 2,
                                    "location_id": "loc-grounding-1",
                                    "text": "Milvus 是向量数据库。",
                                    "supported": True,
                                }
                            ],
                            "claims": [
                                {
                                    "index": 1,
                                    "text": "Milvus 是向量数据库。",
                                    "status": "supported",
                                    "evidence_ids": ["E1"],
                                    "citation_spans": [
                                        {
                                            "evidence_id": "E1",
                                            "start": 0,
                                            "end": len("Milvus 是向量数据库。"),
                                            "text": "Milvus 是向量数据库。",
                                            "match_type": "normalized_exact",
                                            "score": 1.0,
                                        },
                                        {
                                            "evidence_id": "E1",
                                            "start": 0,
                                            "end": 6,
                                            "text": "伪造片段",
                                            "match_type": "normalized_exact",
                                            "score": 1.0,
                                        },
                                    ],
                                },
                                {
                                    "index": 2,
                                    "text": "它支持任意 SQL。",
                                    "status": "invalid_citation",
                                    "evidence_ids": [],
                                    "invalid_evidence_ids": ["E9"],
                                    "citation_spans": [
                                        {
                                            "evidence_id": "E9",
                                            "start": 0,
                                            "end": 999999,
                                            "text": "不得持久化",
                                            "match_type": "normalized_exact",
                                            "score": 1.0,
                                        }
                                    ],
                                },
                            ],
                        }
                    },
                },
            )

        detail = client.get(f"/api/conversations/{conversation['id']}").json()
        persisted = detail["messages"][0]
        assert persisted["answer_state"] == "partially_grounded"
        assert len(persisted["citations"]) == 1
        assert persisted["claims"] == [
            {
                "id": persisted["claims"][0]["id"],
                "index": 1,
                "text": "Milvus 是向量数据库。",
                "support_status": "supported",
                "structural_support_status": "supported",
                "citation_indices": [1],
                "citation_spans": [
                    {
                        "citation_index": 1,
                        "start": 0,
                        "end": len("Milvus 是向量数据库。"),
                        "text": "Milvus 是向量数据库。",
                        "match_type": "normalized_exact",
                        "score": 1.0,
                    }
                ],
                "citation_status": "valid",
                "entailment_status": "not_checked",
                "consistency_status": "not_checked",
                "consistency_checks": [],
                "risk_status": "not_assessed",
                "risk_checks": [],
                "confidence": None,
                "reason_codes": ["CITATION_VALID"],
            },
            {
                "id": persisted["claims"][1]["id"],
                "index": 2,
                "text": "它支持任意 SQL。",
                "support_status": "invalid_citation",
                "structural_support_status": "invalid_citation",
                "citation_indices": [],
                "citation_spans": [],
                "citation_status": "invalid",
                "entailment_status": "not_checked",
                "consistency_status": "not_checked",
                "consistency_checks": [],
                "risk_status": "not_assessed",
                "risk_checks": [],
                "confidence": None,
                "reason_codes": ["CITATION_INVALID"],
            },
        ]


def test_structured_grounding_without_claims_never_falls_back_to_grounded(tmp_path):
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "空声明引用库", "description": ""},
        ).json()
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        with client.product_session_factory() as session:
            assistant = Message(
                conversation_id=conversation["id"],
                role="assistant",
                content="",
                status="pending",
            )
            session.add(assistant)
            session.commit()
            _finish_assistant_message(
                session,
                assistant_message=assistant,
                payload={
                    "result": "```python\nprint(42)\n```",
                    "trace": {
                        "grounding": {
                            "version": 1,
                            "state": "insufficient_evidence",
                            "evidence": [
                                {
                                    "evidence_id": "E1",
                                    "display_name": "code.pdf",
                                    "page_number": 1,
                                    "text": "print(42)",
                                    "supported": True,
                                }
                            ],
                            "claims": [],
                        }
                    },
                },
            )

        persisted = client.get(f"/api/conversations/{conversation['id']}").json()["messages"][0]
        assert persisted["answer_state"] == "insufficient_evidence"
        assert len(persisted["citations"]) == 1
        assert persisted["claims"] == []


def test_trust_contract_and_policy_decision_are_persisted(tmp_path):
    provenance = build_trust_provenance(
        SimpleNamespace(
            config=SimpleNamespace(
                provide_settings={
                    "llm": {"provider": "TestLLM", "config": {"model": "test-model"}},
                    "embedding": {
                        "provider": "TestEmbedding",
                        "config": {"model": "test-embedding"},
                    },
                },
                query_settings={},
                load_settings={},
            ),
            llm=SimpleNamespace(model="test-model"),
            embedding_model=None,
            vector_db=None,
            entailment_checker=None,
        ),
        evidence_snapshot=[],
        execution_scope="online",
        temporal_context=build_temporal_context(
            reference_time="2026-08-11T03:00:00+00:00",
            timezone_name="Asia/Shanghai",
        ),
    )
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "可信策略库", "description": ""},
        ).json()
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        with client.product_session_factory() as session:
            assistant = Message(
                conversation_id=conversation["id"],
                role="assistant",
                content="",
                status="pending",
            )
            session.add(assistant)
            session.commit()
            _finish_assistant_message(
                session,
                assistant_message=assistant,
                payload={
                    "result": "根据现有证据，无法给出有充分依据的回答。",
                    "trace": {
                        "grounding": {
                            "version": 1,
                            "state": "insufficient_evidence",
                            "evidence": [],
                            "claims": [],
                        },
                        "trust": {
                            "version": 1,
                            "trust_status": "insufficient_evidence",
                            "safety_status": "not_evaluated",
                            "verification_level": "semantic_entailment_partial",
                            "evidence_snapshot_available": True,
                            "entailment": {
                                "version": 1,
                                "checker": "llm_nli",
                                "checker_version": "1.0.0",
                                "status": "partial",
                                "token_usage": 17,
                                "eligible_claim_count": 1,
                                "exact_match_count": 0,
                                "checker_claim_count": 1,
                                "entailed_count": 0,
                                "contradicted_count": 0,
                                "unknown_count": 1,
                                "not_checked_count": 0,
                                "error_code": None,
                            },
                            "risk": {
                                "version": 1,
                                "classifier": "deterministic_query_risk",
                                "classifier_version": "1.1.0",
                                "risk_level": "high",
                                "query_type": "financial_policy",
                                "risk_factors": [
                                    "FINANCIAL_DOMAIN",
                                    "DECISION_REQUEST",
                                    "QUANTITATIVE_DECISION",
                                ],
                                "requirements": {
                                    "require_citation": True,
                                    "require_decisive_entailment": True,
                                    "minimum_evidence_count": 2,
                                    "minimum_distinct_source_count": 2,
                                    "allow_unknown_entailment": False,
                                },
                            },
                            "freshness": {
                                "version": 1,
                                "classifier": "deterministic_freshness_intent",
                                "classifier_version": "1.1.0",
                                "required": True,
                                "mode": "latest_effective",
                                "ordering_basis": "effective_at",
                                "reason_codes": ["FRESHNESS_LATEST_EFFECTIVE_REQUESTED"],
                            },
                            "temporal_context": {
                                "version": 1,
                                "source": "request_clock",
                                "reference_time": "2026-08-11T03:00:00+00:00",
                                "reference_date": "2026-08-11",
                                "timezone": "Asia/Shanghai",
                            },
                            "input": {
                                "trust_status": "insufficient_evidence",
                                "claim_count": 1,
                                "supported_claim_count": 0,
                                "claims": [
                                    {
                                        "index": 1,
                                        "text": "每份 PDF 最大 100 MB。",
                                        "support_status": "unsupported",
                                        "structural_support_status": "supported",
                                        "citation_status": "valid",
                                        "entailment_status": "not_checked",
                                        "entailment_method": "semantic_nli",
                                        "entailment_checker": "llm_nli",
                                        "entailment_checker_version": "1.0.0",
                                        "confidence": 0.61,
                                        "risk_status": "rejected",
                                        "risk_checks": [
                                            {
                                                "kind": "entailment",
                                                "status": "failed",
                                                "reason_code": "HIGH_RISK_ENTAILMENT_REQUIRED",
                                                "actual": "unknown",
                                                "required": "entailed_or_contradicted",
                                            }
                                        ],
                                        "consistency_status": "inconsistent",
                                        "consistency_checks": [
                                            {
                                                "kind": "quantity",
                                                "status": "inconsistent",
                                                "reason_code": "QUANTITY_NOT_IN_EVIDENCE",
                                                "claim_values": ["100|mb"],
                                                "missing_values": ["100|mb"],
                                            }
                                        ],
                                        "reason_codes": [
                                            "CITATION_VALID",
                                            "QUANTITY_NOT_IN_EVIDENCE",
                                        ],
                                    }
                                ],
                            },
                            "output": {
                                "trust_status": "insufficient_evidence",
                                "claim_count": 1,
                                "supported_claim_count": 0,
                                "claims": [],
                            },
                            "claims": [],
                            "policy": {
                                "profile": "strict_high_risk",
                                "risk_level": "high",
                                "action": "refuse",
                                "reason_codes": ["EVIDENCE_INSUFFICIENT"],
                            },
                            "limitations": [
                                "SEMANTIC_ENTAILMENT_NOT_CHECKED",
                                "SAFETY_NOT_EVALUATED",
                            ],
                            "provenance": provenance,
                        },
                    },
                },
            )

        persisted = client.get(f"/api/conversations/{conversation['id']}").json()["messages"][0]
        assert persisted["trust_contract_version"] == 1
        assert persisted["trust_status"] == "insufficient_evidence"
        assert persisted["safety_status"] == "not_evaluated"
        assert persisted["policy_action"] == "refuse"
        assert persisted["policy_profile"] == "strict_high_risk"
        assert persisted["policy_reason_codes"] == ["EVIDENCE_INSUFFICIENT"]
        assert persisted["risk_level"] == "high"
        assert persisted["query_type"] == "financial_policy"
        assert persisted["risk_factors"] == [
            "FINANCIAL_DOMAIN",
            "DECISION_REQUEST",
            "QUANTITATIVE_DECISION",
        ]
        assert persisted["provenance_contract_version"] == 2
        assert persisted["provenance_digest"] == provenance["digest"]
        assert persisted["trust_details"]["provenance"] == provenance
        assert persisted["trust_details"]["provenance"]["evidence"] == {
            "snapshot_status": "complete",
            "evidence_count": 0,
            "knowledge_base_count": 0,
            "web_count": 0,
            "snapshot_fingerprint": provenance["evidence"]["snapshot_fingerprint"],
            "items": [],
        }
        assert persisted["trust_details"]["verification_level"] == ("semantic_entailment_partial")
        assert persisted["trust_details"]["entailment"] == {
            "version": 1,
            "checker": "llm_nli",
            "checker_version": "1.0.0",
            "status": "partial",
            "token_usage": 17,
            "eligible_claim_count": 1,
            "exact_match_count": 0,
            "checker_claim_count": 1,
            "entailed_count": 0,
            "contradicted_count": 0,
            "unknown_count": 1,
            "not_checked_count": 0,
        }
        assert persisted["trust_details"]["risk"]["requirements"] == {
            "require_citation": True,
            "require_decisive_entailment": True,
            "minimum_evidence_count": 2,
            "minimum_distinct_source_count": 2,
            "allow_unknown_entailment": False,
        }
        assert persisted["trust_details"]["freshness"] == {
            "version": 1,
            "classifier": "deterministic_freshness_intent",
            "classifier_version": "1.1.0",
            "required": True,
            "mode": "latest_effective",
            "ordering_basis": "effective_at",
            "reason_codes": ["FRESHNESS_LATEST_EFFECTIVE_REQUESTED"],
        }
        assert persisted["trust_details"]["temporal_context"] == {
            "version": 1,
            "source": "request_clock",
            "reference_time": "2026-08-11T03:00:00+00:00",
            "reference_date": "2026-08-11",
            "timezone": "Asia/Shanghai",
        }
        finding = persisted["trust_details"]["input"]["claims"][0]
        assert finding["structural_support_status"] == "supported"
        assert finding["support_status"] == "unsupported"
        assert finding["consistency_checks"][0]["missing_values"] == ["100|mb"]
        assert finding["entailment_method"] == "semantic_nli"
        assert finding["entailment_checker"] == "llm_nli"
        assert finding["confidence"] == 0.61
        assert finding["risk_status"] == "rejected"
        assert finding["risk_checks"][0]["actual"] == "unknown"


def test_create_list_and_select_knowledge_bases(tmp_path):
    with product_client(tmp_path) as client:
        first = client.post(
            "/api/knowledge-bases",
            json={"name": "AI 学习资料", "description": "课程与项目资料"},
        )
        second = client.post(
            "/api/knowledge-bases",
            json={"name": "产品文档", "description": ""},
        )

        assert first.status_code == 201
        assert first.json()["is_current"] is True
        assert second.status_code == 201
        assert second.json()["is_current"] is False

        selected = client.put(f"/api/knowledge-bases/{second.json()['id']}/current")
        assert selected.status_code == 200
        assert selected.json()["is_current"] is True

        items = client.get("/api/knowledge-bases").json()["items"]
        assert items[0]["name"] == "产品文档"
        assert sum(item["is_current"] for item in items) == 1


def test_product_errors_use_safe_structured_shape(tmp_path):
    with product_client(tmp_path) as client:
        response = client.get("/api/knowledge-bases/kb_missing")

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"].pop("request_id")
    assert payload == {
        "error": {
            "code": "KNOWLEDGE_BASE_NOT_FOUND",
            "message": "没有找到这个知识库。",
            "retryable": False,
        }
    }


def test_document_upload_rejects_non_pdf(tmp_path):
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "资料库", "description": ""},
        ).json()
        response = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("note.txt", b"plain text", "text/plain")},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DOCUMENT_UNSUPPORTED_TYPE"


def test_document_upload_persists_explicit_business_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "制度资料", "description": ""},
        ).json()
        response = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("policy.pdf", b"%PDF-1.7\npolicy", "application/pdf")},
            data={
                "published_at": "2026-08-11",
                "effective_at": "2026-08-12",
                "superseded_at": "2027-01-01",
                "version_family": "Travel Expense Policy",
            },
        )

    assert response.status_code == 202
    document = response.json()["document"]
    assert document["published_at"] == "2026-08-11"
    assert document["effective_at"] == "2026-08-12"
    assert document["superseded_at"] == "2027-01-01"
    assert document["temporal_metadata_source"] == "user_declared"
    assert document["version_family"] == "travel-expense-policy"
    assert document["version_family_source"] == "user_declared"


def test_document_upload_rejects_superseded_date_before_effective_date(tmp_path):
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "制度资料", "description": ""},
        ).json()
        response = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("policy.pdf", b"%PDF-1.7\npolicy", "application/pdf")},
            data={
                "effective_at": "2026-08-12",
                "superseded_at": "2026-08-11",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DOCUMENT_TEMPORAL_METADATA_INVALID"


def test_updating_ready_document_business_dates_queues_metadata_reindex(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "制度资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("policy.pdf", b"%PDF-1.7\npolicy", "application/pdf")},
        ).json()
        document_id = upload["document"]["id"]
        with client.product_session_factory() as session:
            stored = session.get(Document, document_id)
            assert stored is not None
            stored.status = "ready"
            session.commit()

        response = client.patch(
            f"/api/documents/{document_id}/governance-metadata",
            json={
                "published_at": "2026-08-11",
                "effective_at": "2026-08-12",
                "superseded_at": None,
                "version_family": "Travel Expense Policy",
            },
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document"]["status"] == "queued"
    assert payload["document"]["published_at"] == "2026-08-11"
    assert payload["document"]["version_family"] == "travel-expense-policy"
    assert payload["document"]["version_family_source"] == "user_declared"
    assert payload["job"]["attempt"] == 2


def test_document_worker_sends_temporal_metadata_to_core_ingest(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    source_dir = upload_root / "kb"
    source_dir.mkdir(parents=True)
    source = source_dir / "policy.pdf"
    source.write_bytes(b"%PDF-1.7\npolicy")
    monkeypatch.setattr(document_service, "UPLOAD_DIR", upload_root)
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"collection": {"manifest": {"schema_version": 2}}}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json):
            captured["url"] = url
            captured["json"] = json
            return Response()

    monkeypatch.setattr(document_service.httpx, "AsyncClient", Client)
    document = SimpleNamespace(
        storage_path=str(source),
        sha256="a" * 64,
        published_at=date(2026, 8, 11),
        effective_at=date(2026, 8, 12),
        superseded_at=None,
        temporal_metadata_source="user_declared",
        version_family="travel-expense-policy",
        version_family_source="user_declared",
    )
    knowledge_base = SimpleNamespace(collection_name="kb_" + "a" * 32)

    manifest = asyncio.run(
        document_service._load_document_into_backend(
            document=document,
            knowledge_base=knowledge_base,
        )
    )

    assert manifest == {"schema_version": 2}
    assert captured["json"]["document_metadata"] == {
        "published_at": "2026-08-11",
        "effective_at": "2026-08-12",
        "temporal_metadata_source": "user_declared",
        "version_family": "travel-expense-policy",
        "version_family_source": "user_declared",
    }


def test_document_content_returns_inline_original_pdf(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", upload_dir)
    pdf_payload = b"%PDF-1.7\ninline source"

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "原文资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", pdf_payload, "application/pdf")},
        ).json()

        response = client.get(f"/api/documents/{upload['document']['id']}/content")

    assert response.status_code == 200
    assert response.content == pdf_payload
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith('inline; filename="guide.pdf"')
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_document_content_rejects_storage_path_outside_upload_root(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", upload_dir)
    outside_path = tmp_path / "outside.pdf"
    outside_path.write_bytes(b"%PDF-1.7\noutside")

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "异常路径资料", "description": ""},
        ).json()
        with client.product_session_factory() as session:
            document = Document(
                knowledge_base_id=knowledge_base["id"],
                display_name="outside.pdf",
                storage_path=str(outside_path),
                size_bytes=outside_path.stat().st_size,
                sha256="f" * 64,
                status="ready",
            )
            session.add(document)
            session.commit()
            document_id = document.id

        response = client.get(f"/api/documents/{document_id}/content")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DOCUMENT_STORAGE_INVALID"


def test_delete_document_removes_vectors_file_and_database_record(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", upload_dir)
    captured = {}
    deleted_manifest = {
        "schema_version": 1,
        "embedding_model": "embedding-a",
        "data_version": "after-delete",
    }

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"delete_count": 2, "manifest": deleted_manifest}

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def delete(self, url, *, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return Response()

    monkeypatch.setattr("frontend.product.services.documents.httpx.AsyncClient", Client)

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "待清理资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", b"%PDF-1.7\ndelete me", "application/pdf")},
        ).json()
        document_id = upload["document"]["id"]

        with client.product_session_factory() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.status = "ready"
            storage_path = document.storage_path
            source_hash = document.sha256
            conversation = Conversation(knowledge_base_id=knowledge_base["id"])
            message = Message(
                conversation=conversation,
                role="assistant",
                content="带历史引用的回答",
                status="succeeded",
            )
            citation = Citation(
                message=message,
                document_id=document.id,
                index=1,
                display_name=document.display_name,
                page_number=1,
                text="历史引用文字",
            )
            session.add(conversation)
            session.commit()
            citation_id = citation.id

        response = client.delete(f"/api/documents/{document_id}")

        assert response.status_code == 204
        assert client.get(f"/api/documents/{document_id}").status_code == 404
        detail = client.get(f"/api/knowledge-bases/{knowledge_base['id']}").json()
        assert detail["index_manifest"] == deleted_manifest
        with client.product_session_factory() as session:
            retained_citation = session.get(Citation, citation_id)
            assert retained_citation is not None
            assert retained_citation.document_id is None
            assert retained_citation.display_name == "guide.pdf"
            assert retained_citation.text == "历史引用文字"

    assert not Path(storage_path).exists()
    assert source_hash in captured["url"]
    assert captured["url"].endswith(source_hash)
    assert captured["client_kwargs"] == {
        "timeout": 30.0,
        "trust_env": False,
        "headers": {"X-DeepSearcher-Tenant": "local"},
    }
    assert storage_path


def test_delete_document_blocks_processing_document(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "处理中资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", b"%PDF-1.7\nbusy", "application/pdf")},
        ).json()

        response = client.delete(f"/api/documents/{upload['document']['id']}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DOCUMENT_BUSY"
        assert client.get(f"/api/documents/{upload['document']['id']}").status_code == 200


def test_delete_document_keeps_local_record_when_vector_delete_fails(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", upload_dir)

    class Response:
        is_success = False

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def delete(self, _url):
            return Response()

    monkeypatch.setattr("frontend.product.services.documents.httpx.AsyncClient", Client)

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "删除失败资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", b"%PDF-1.7\nkeep me", "application/pdf")},
        ).json()
        document_id = upload["document"]["id"]

        with client.product_session_factory() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.status = "ready"
            storage_path = document.storage_path
            session.commit()

        response = client.delete(f"/api/documents/{document_id}")

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "DOCUMENT_VECTOR_DELETE_FAILED"
        assert client.get(f"/api/documents/{document_id}").status_code == 200
        assert Path(storage_path).exists()
        assert storage_path


def test_delete_knowledge_base_cascades_data_files_vectors_and_selects_fallback(
    tmp_path,
    monkeypatch,
):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", upload_dir)
    captured = {}

    class Response:
        is_success = True

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def delete(self, url, *, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return Response()

    monkeypatch.setattr(
        "frontend.product.services.knowledge_bases.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        deleting = client.post(
            "/api/knowledge-bases",
            json={"name": "待删除知识库", "description": ""},
        ).json()
        fallback = client.post(
            "/api/knowledge-bases",
            json={"name": "回退知识库", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{deleting['id']}/documents",
            files={"file": ("guide.pdf", b"%PDF-1.7\ncascade", "application/pdf")},
        ).json()
        document_id = upload["document"]["id"]

        with client.product_session_factory() as session:
            stored_knowledge_base = session.get(KnowledgeBase, deleting["id"])
            assert stored_knowledge_base is not None
            collection_name = stored_knowledge_base.collection_name
            document = session.get(Document, document_id)
            assert document is not None
            document.status = "ready"
            conversation = Conversation(
                knowledge_base_id=deleting["id"],
                title="待级联对话",
            )
            message = Message(
                conversation=conversation,
                role="assistant",
                content="待级联回答",
                status="succeeded",
            )
            citation = Citation(
                message=message,
                document_id=document.id,
                index=1,
                display_name=document.display_name,
                text="待级联引用",
            )
            session.add(conversation)
            session.commit()
            conversation_id = conversation.id
            message_id = message.id
            citation_id = citation.id

        detail = client.get(f"/api/knowledge-bases/{deleting['id']}").json()
        assert detail["document_count"] == 1
        assert detail["conversation_count"] == 1

        response = client.delete(f"/api/knowledge-bases/{deleting['id']}")

        assert response.status_code == 200
        assert response.json() == {
            "deleted_id": deleting["id"],
            "current_knowledge_base_id": fallback["id"],
        }
        assert client.get(f"/api/knowledge-bases/{deleting['id']}").status_code == 404
        assert client.get(f"/api/documents/{document_id}").status_code == 404
        assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
        assert client.get(f"/api/knowledge-bases/{fallback['id']}").json()["is_current"]
        with client.product_session_factory() as session:
            assert session.get(Message, message_id) is None
            assert session.get(Citation, citation_id) is None

    assert not upload_dir.joinpath(deleting["id"]).exists()
    assert collection_name in captured["url"]
    assert captured["url"].endswith(collection_name)
    assert captured["headers"] == {"X-Confirm-Collection": collection_name}
    assert captured["client_kwargs"] == {
        "timeout": 30.0,
        "trust_env": False,
        "headers": {"X-DeepSearcher-Tenant": "local"},
    }


def test_delete_knowledge_base_blocks_while_document_is_processing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "处理中知识库", "description": ""},
        ).json()
        client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", b"%PDF-1.7\nbusy", "application/pdf")},
        )

        response = client.delete(f"/api/knowledge-bases/{knowledge_base['id']}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_BUSY"
        assert client.get(f"/api/knowledge-bases/{knowledge_base['id']}").status_code == 200


def test_reindex_knowledge_base_sends_all_sources_and_persists_manifest(
    tmp_path,
    monkeypatch,
):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", upload_dir)
    captured = {}
    manifest = {
        "schema_version": 1,
        "embedding_provider": "TestEmbedding",
        "embedding_model": "embedding-a",
        "embedding_version": "embedding-a-v1",
        "dimension": 8,
        "normalization": "none",
        "metric_type": "L2",
        "chunk_config_version": "chunk-v1",
        "document_version": "document-v1",
        "data_version": "data-v1",
    }

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "collection": {
                    "manifest": manifest,
                    "previous_collection": "kb_previous_version",
                }
            }

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return Response()

    monkeypatch.setattr(
        "frontend.product.services.knowledge_bases.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "待重建知识库", "description": "完整资料"},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", b"%PDF-1.7\nreindex", "application/pdf")},
        ).json()
        with client.product_session_factory() as session:
            document = session.get(Document, upload["document"]["id"])
            assert document is not None
            document.status = "failed"
            document.error_code = "DOCUMENT_PROCESSING_FAILED"
            document.error_message = "旧索引处理失败"
            source_path = document.storage_path
            collection_name = document.knowledge_base.collection_name
            session.commit()

        response = client.post(f"/api/knowledge-bases/{knowledge_base['id']}/reindex")

        assert response.status_code == 200
        assert response.json()["index_status"] == "verified"
        assert response.json()["index_manifest"] == manifest
        assert response.json()["index_previous_collection"] == "kb_previous_version"
        assert response.json()["ready_document_count"] == 1
        listed = client.get("/api/knowledge-bases").json()["items"][0]
        assert listed["index_status"] == "verified"
        assert listed["index_manifest"] == manifest
        with client.product_session_factory() as session:
            rebuilt_document = session.get(Document, upload["document"]["id"])
            assert rebuilt_document is not None
            assert rebuilt_document.status == "ready"
            assert rebuilt_document.error_code is None
            assert rebuilt_document.error_message is None

    assert captured["url"].endswith(f"/collections/{collection_name}/rebuild")
    assert captured["headers"] == {"X-Confirm-Collection": collection_name}
    assert captured["json"] == {
        "paths": [source_path],
        "document_metadata": [{}],
        "collection_description": "完整资料",
        "batch_size": 10,
    }
    assert captured["client_kwargs"] == {
        "timeout": 600.0,
        "trust_env": False,
        "headers": {"X-DeepSearcher-Tenant": "local"},
    }


def test_delete_conversation_cascades_messages_and_citations_only(tmp_path):
    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "保留知识库", "description": ""},
        ).json()
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        with client.product_session_factory() as session:
            stored_conversation = session.get(Conversation, conversation["id"])
            message = Message(
                conversation=stored_conversation,
                role="assistant",
                content="待删除回答",
                status="succeeded",
            )
            citation = Citation(
                message=message,
                index=1,
                display_name="历史资料.pdf",
                text="待删除引用",
            )
            session.add(message)
            session.commit()
            message_id = message.id
            citation_id = citation.id

        response = client.delete(f"/api/conversations/{conversation['id']}")

        assert response.status_code == 204
        assert client.get(f"/api/conversations/{conversation['id']}").status_code == 404
        assert client.get(f"/api/knowledge-bases/{knowledge_base['id']}").status_code == 200
        with client.product_session_factory() as session:
            assert session.get(Message, message_id) is None
            assert session.get(Citation, citation_id) is None


def test_conversation_history_excludes_failed_and_weakly_grounded_answers():
    conversation = Conversation(knowledge_base_id="kb_test", title="上下文测试")
    conversation.messages = [
        Message(role="user", content="Milvus 有哪些部署方式？", status="succeeded"),
        Message(
            role="assistant",
            content="包括单机部署和集群部署。",
            status="succeeded",
            answer_state="fully_grounded",
        ),
        Message(role="user", content="它们有什么区别？", status="succeeded"),
        Message(
            role="assistant",
            content="这是一条只有部分依据的回答。",
            status="succeeded",
            answer_state="partially_grounded",
        ),
        Message(role="user", content="再说说扩缩容。", status="succeeded"),
        Message(
            role="assistant",
            content="调用失败的错误文字。",
            status="failed",
            answer_state="failed",
        ),
    ]

    assert build_conversation_history(conversation) == [
        {
            "role": "user",
            "content": "Milvus 有哪些部署方式？",
            "grounded": False,
        },
        {
            "role": "assistant",
            "content": "包括单机部署和集群部署。",
            "grounded": True,
        },
        {"role": "user", "content": "它们有什么区别？", "grounded": False},
        {"role": "user", "content": "再说说扩缩容。", "grounded": False},
    ]


def test_upload_query_and_citation_flow_uses_internal_collection_scope(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )
    pdf_payload = b"%PDF-1.7\nproduct test"
    source_hash = hashlib.sha256(pdf_payload).hexdigest()
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "result": "DeepSearcher 会先理解问题，再检索并生成答案。",
                "consume_token": 42,
                "trace": {
                    "version": 1,
                    "iterations": [
                        {
                            "retrieved_documents": [
                                {
                                    "document_id": source_hash,
                                    "display_name": "guide.pdf",
                                    "page_number": 2,
                                    "chunk_index": 3,
                                    "section_title": "查询流程",
                                    "section_path": ["使用指南", "查询流程"],
                                    "char_start": 120,
                                    "char_end": 148,
                                    "bbox": [0.1, 0.2, 0.9, 0.3],
                                    "location_id": "location-query-flow",
                                    "source_locator": "page=2&char=120-148",
                                    "parser_version": "pdfplumber-layout-v2+rapidocr-v3",
                                    "extraction_method": "text",
                                    "source_type": "knowledge_base",
                                    "source_url": None,
                                    "source_domain": None,
                                    "trusted": True,
                                    "text": "DeepSearcher 查询流程说明。",
                                    "supported": True,
                                }
                            ]
                        }
                    ],
                },
            }

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return Response()

    monkeypatch.setattr(
        "frontend.product.services.conversations.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "AI 全栈学习资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("guide.pdf", pdf_payload, "application/pdf")},
        )

        assert upload.status_code == 202
        product_document_id = upload.json()["document"]["id"]
        assert upload.json()["document"]["status"] == "queued"
        job_id = upload.json()["job"]["id"]
        persisted_job = client.get(f"/api/ingest-jobs/{job_id}")
        assert persisted_job.status_code == 200
        assert persisted_job.json()["document_id"] == product_document_id
        assert persisted_job.json()["status"] == "queued"

        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()
        answer = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            headers={"X-Request-ID": "product-query-test-1"},
            json={"content": "DeepSearcher 的查询流程是什么？"},
        )

        assert answer.status_code == 200
        assistant = answer.json()["assistant_message"]
        assert assistant["status"] == "succeeded"
        assert assistant["answer_state"] == "grounded"
        assert assistant["citations"] == [
            {
                "id": assistant["citations"][0]["id"],
                "index": 1,
                "document_id": product_document_id,
                "display_name": "guide.pdf",
                "page_number": 2,
                "chunk_index": 3,
                "section_title": "查询流程",
                "section_path": ["使用指南", "查询流程"],
                "char_start": 120,
                "char_end": 148,
                "bbox": [0.1, 0.2, 0.9, 0.3],
                "location_id": "location-query-flow",
                "source_locator": "page=2&char=120-148",
                "parser_version": "pdfplumber-layout-v2+rapidocr-v3",
                "extraction_method": "text",
                "source_type": "knowledge_base",
                "source_url": None,
                "source_domain": None,
                "trusted": True,
                "published_at": None,
                "effective_at": None,
                "superseded_at": None,
                "temporal_metadata_source": None,
                "version_family": None,
                "version_family_source": None,
                "text": "DeepSearcher 查询流程说明。",
                "supported": True,
            }
        ]

        detail = client.get(f"/api/conversations/{conversation['id']}").json()
        assert len(detail["messages"]) == 2
        persisted_citation = detail["messages"][1]["citations"][0]
        assert persisted_citation["page_number"] == 2
        assert persisted_citation["section_title"] == "查询流程"
        assert persisted_citation["location_id"] == "location-query-flow"

    assert captured["url"].endswith("/query")
    assert captured["json"]["collection_names"][0].startswith("kb_")
    assert captured["json"]["original_query"] == "DeepSearcher 的查询流程是什么？"
    assert captured["json"]["conversation_history"] == []
    assert captured["json"]["include_trace"] is True
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["client_kwargs"]["headers"]["X-Request-ID"] == "product-query-test-1"


def test_product_query_stream_relays_safe_stages_and_persists_completion(
    tmp_path,
    monkeypatch,
):
    captured = {}
    upstream_events = [
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 1,
            "event": "started",
            "data": {
                "stage": "query_started",
                "api_key": "should-not-reach-browser",
                "path": r"C:\Users\private\notes.pdf",
            },
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 2,
            "event": "contextualization",
            "data": {
                "depends_on_history": True,
                "history_turn_count": 4,
                "fallback_used": False,
                "reason": "rewritten",
                "token_usage": 19,
                "standalone_query": "private rewritten query",
            },
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 3,
            "event": "routing",
            "data": {
                "agent": "DeepSearch",
                "fallback_used": False,
                "subquery": "private intermediate query",
            },
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 4,
            "event": "retrieval",
            "data": {
                "iteration": 1,
                "retrieved_count": 4,
                "metadata": {"source_path": r"C:\Users\private\notes.pdf"},
            },
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 5,
            "event": "web_search",
            "data": {
                "iteration": 1,
                "status": "completed",
                "provider": "tavily",
                "query_count": 1,
                "result_count": 1,
                "error_code": None,
                "api_key": "should-not-reach-browser",
            },
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 6,
            "event": "support",
            "data": {"iteration": 1, "supported_count": 1},
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 7,
            "event": "reflection",
            "data": {"iteration": 1, "has_enough_information": True},
        },
        {
            "version": 1,
            "request_id": "product-stream-test-1",
            "sequence": 8,
            "event": "completed",
            "data": {
                "result": "这是基于资料生成的安全回答。",
                "trace": {
                    "version": 3,
                    "iterations": [
                        {
                            "retrieved_documents": [
                                {
                                    "display_name": "guide.pdf",
                                    "page_number": 2,
                                    "text": "可核对的证据片段。",
                                    "supported": True,
                                },
                                {
                                    "display_name": "Tavily 官方文档",
                                    "reference": "https://docs.tavily.com/search",
                                    "source_type": "web",
                                    "source_url": "https://docs.tavily.com/search",
                                    "source_domain": "docs.tavily.com",
                                    "trusted": True,
                                    "text": "可核对的网页证据片段。",
                                    "supported": True,
                                },
                            ]
                        }
                    ],
                    "private_debug": "must-not-reach-browser",
                },
            },
        },
    ]

    class Response:
        is_success = True

        async def aiter_lines(self):
            for envelope in upstream_events:
                yield f"event: {envelope['event']}"
                yield f"data: {json.dumps(envelope)}"
                yield ""

    class StreamContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, *, json):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return StreamContext()

    monkeypatch.setattr(
        "frontend.product.services.conversations.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "实时回答知识库", "description": ""},
        ).json()
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        response = client.post(
            f"/api/conversations/{conversation['id']}/messages/stream",
            headers={"X-Request-ID": "product-stream-test-1"},
            json={"content": "请给出可核对的回答", "use_web_search": True},
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, no-transform"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["x-trace-retention"] == "transient"
        assert response.headers["x-request-id"] == "product-stream-test-1"
        events = parse_sse_events(response.text)
        assert [event["event"] for event in events] == [
            "started",
            "contextualization",
            "routing",
            "retrieval",
            "web_search",
            "support",
            "reflection",
            "completed",
        ]
        assert events[1]["data"] == {
            "depends_on_history": True,
            "history_turn_count": 4,
            "fallback_used": False,
            "reason": "rewritten",
        }
        assert events[3]["data"] == {"iteration": 1, "retrieved_count": 4}
        assert events[4]["data"] == {
            "iteration": 1,
            "status": "completed",
            "provider": "tavily",
            "query_count": 1,
            "result_count": 1,
            "error_code": None,
        }
        assert set(events[-1]["data"]) == {"user_message", "assistant_message"}
        assert events[-1]["data"]["assistant_message"]["content"] == (
            "这是基于资料生成的安全回答。"
        )
        assert events[-1]["data"]["assistant_message"]["citations"][0]["text"] == (
            "可核对的证据片段。"
        )
        for private_value in (
            "should-not-reach-browser",
            r"C:\Users\private\notes.pdf",
            "private intermediate query",
            "private rewritten query",
            "must-not-reach-browser",
            "private_debug",
        ):
            assert private_value not in response.text

        detail = client.get(f"/api/conversations/{conversation['id']}").json()
        assert [message["status"] for message in detail["messages"]] == [
            "succeeded",
            "succeeded",
        ]
        assert detail["messages"][1]["citations"][0]["display_name"] == "guide.pdf"
        web_citation = detail["messages"][1]["citations"][1]
        assert web_citation["source_type"] == "web"
        assert web_citation["source_url"] == "https://docs.tavily.com/search"
        assert web_citation["source_domain"] == "docs.tavily.com"
        assert web_citation["trusted"] is True
        assert web_citation["document_id"] is None

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/query/stream")
    assert captured["json"]["collection_names"][0].startswith("kb_")
    assert captured["json"]["use_web_search"] is True
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["client_kwargs"]["headers"]["X-Request-ID"] == "product-stream-test-1"


def test_closing_product_query_stream_marks_pending_answer_as_stopped(
    tmp_path,
    monkeypatch,
):
    upstream_closed = {"value": False}
    started = {
        "version": 1,
        "request_id": "request-cancel-1",
        "sequence": 1,
        "event": "started",
        "data": {"stage": "query_started"},
    }

    class Response:
        is_success = True

        async def aiter_lines(self):
            yield "event: started"
            yield f"data: {json.dumps(started)}"
            yield ""

    class StreamContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            upstream_closed["value"] = True

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return StreamContext()

    monkeypatch.setattr(
        "frontend.product.services.conversations.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "中断回答知识库", "description": ""},
        ).json()
        conversation_payload = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        with client.product_session_factory() as session:
            conversation = get_conversation(session, conversation_payload["id"])
            assert conversation is not None

            async def consume_then_close():
                stream = stream_message_events(
                    session,
                    conversation=conversation,
                    content="开始后立即停止",
                )
                first = await anext(stream)
                await stream.aclose()
                return first

            first_event = asyncio.run(consume_then_close())

        assert first_event["event"] == "started"
        assert upstream_closed["value"] is True
        detail = client.get(f"/api/conversations/{conversation_payload['id']}").json()
        assert [message["status"] for message in detail["messages"]] == [
            "succeeded",
            "failed",
        ]
        assert detail["messages"][1]["content"] == "本次回答已停止。"


def test_query_error_mapping_preserves_vector_failure_categories():
    class Response:
        def __init__(self, code):
            self.code = code

        def json(self):
            return {"error": {"code": self.code}}

    cases = [
        ("RUNTIME_INITIALIZATION_FAILED", 503, True, "正在恢复依赖"),
        ("VECTOR_DB_UNAVAILABLE", 503, True, "暂时不可用"),
        ("VECTOR_COLLECTION_NOT_FOUND", 409, False, "索引不存在"),
        ("VECTOR_DIMENSION_MISMATCH", 409, False, "不兼容"),
        ("VECTOR_SEARCH_FAILED", 502, True, "没有完成"),
    ]

    for code, status_code, retryable, message_fragment in cases:
        error = query_error_from_response(Response(code))
        assert error.code == code
        assert error.status_code == status_code
        assert error.retryable is retryable
        assert message_fragment in error.message


def test_vector_database_failure_is_visible_and_persisted_as_failed_answer(
    tmp_path,
    monkeypatch,
):
    class Response:
        is_success = False

        @staticmethod
        def json():
            return {
                "error": {
                    "code": "VECTOR_DB_UNAVAILABLE",
                    "message": "internal upstream message",
                    "retryable": True,
                }
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            assert json
            return Response()

    monkeypatch.setattr(
        "frontend.product.services.conversations.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "故障可见知识库", "description": ""},
        ).json()
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        response = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "这个问题不能被伪装成无答案"},
        )

        assert response.status_code == 503
        payload = response.json()
        assert payload["error"].pop("request_id")
        assert payload == {
            "error": {
                "code": "VECTOR_DB_UNAVAILABLE",
                "message": "向量检索服务暂时不可用，请稍后重试。",
                "retryable": True,
            }
        }
        detail = client.get(f"/api/conversations/{conversation['id']}").json()
        assert len(detail["messages"]) == 2
        assistant = detail["messages"][1]
        assert assistant["status"] == "failed"
        assert assistant["answer_state"] == "failed"
        assert assistant["content"] == "向量检索服务暂时不可用，请稍后重试。"
        assert assistant["citations"] == []


def test_citation_maps_product_document_id_back_to_original_filename(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {
                "result": "Milvus 是向量数据库。",
                "trace": {
                    "iterations": [
                        {
                            "retrieved_documents": [
                                {
                                    "document_id": captured["document_id"],
                                    "display_name": f"{captured['document_id']}.pdf",
                                    "page_number": 1,
                                    "text": "What is Milvus?",
                                    "supported": True,
                                }
                            ]
                        }
                    ]
                },
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            assert json
            return Response()

    monkeypatch.setattr(
        "frontend.product.services.conversations.httpx.AsyncClient",
        Client,
    )

    with product_client(tmp_path) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "Milvus 资料", "description": ""},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            files={
                "file": (
                    "WhatisMilvus.pdf",
                    b"%PDF-1.7\ncitation mapping",
                    "application/pdf",
                )
            },
        ).json()
        captured["document_id"] = upload["document"]["id"]
        conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": knowledge_base["id"]},
        ).json()

        answer = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "Milvus 是什么？"},
        ).json()

    citation = answer["assistant_message"]["citations"][0]
    assert citation["document_id"] == captured["document_id"]
    assert citation["display_name"] == "WhatisMilvus.pdf"
