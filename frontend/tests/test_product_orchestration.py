"""Product V2 routing, context, and stream orchestration contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from frontend.product.auth import require_user
from frontend.product.db import Base, create_database_engine, get_session
from frontend.product.models import AnswerRun, Document, KnowledgeBase, User
from frontend.product.services import conversations as conversation_service
from frontend.product.services.context_policy import ContextPolicy
from frontend.server import app


@pytest.fixture
def product_client(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'orchestration.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        user = User(
            username="orchestration-user",
            display_name="编排测试用户",
            password_hash="disabled",
            role="admin",
        )
        session.add(user)
        session.commit()

    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[require_user] = lambda: user
    try:
        client = TestClient(app)
        client.product_session_factory = session_factory
        yield client
    finally:
        app.dependency_overrides.clear()


def _conversation(client: TestClient, *, name: str = "编排知识库") -> dict:
    knowledge_base = client.post(
        "/api/knowledge-bases",
        json={"name": name, "description": ""},
    ).json()
    with client.product_session_factory() as session:
        document = Document(
            knowledge_base_id=knowledge_base["id"],
            display_name="fixture.txt",
            storage_path="fixture.txt",
            size_bytes=1,
            sha256=(name.encode("utf-8").hex() + "0" * 64)[:64],
            status="ready",
        )
        session.add(document)
        session.get(KnowledgeBase, knowledge_base["id"]).index_manifest = json.dumps(
            {"schema_version": 1}
        )
        session.commit()
    return client.post(
        "/api/conversations",
        json={"knowledge_base_id": knowledge_base["id"]},
    ).json()


class _Response:
    is_success = True

    def __init__(self, payload: dict, *, is_success: bool = True):
        self._payload = payload
        self.is_success = is_success

    def json(self):
        return self._payload

    async def aread(self):
        return b""


class _StreamResponse:
    is_success = True

    def __init__(self, events: list[dict]):
        self.events = events

    async def aiter_lines(self):
        for envelope in self.events:
            yield f"event: {envelope['event']}"
            yield f"data: {json.dumps(envelope, ensure_ascii=False)}"
            yield ""


class _StreamContext:
    def __init__(self, response: _StreamResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return None


def _route(mode: str, *, factors: list[str] | None = None) -> dict:
    intent = {
        "chat": "general_question",
        "knowledge": "enterprise_fact",
        "web": "external_fact",
    }[mode]
    return {
        "answer_mode": mode,
        "route_intent": intent,
        "route_source": "rule",
        "reason_code": "test_route",
        "confidence": 0.95,
        "router_version": "query-router-v1",
        "initial_risk_level": "high" if factors else "medium",
        "initial_risk_factors": factors or [],
    }


def _query_result() -> dict:
    return {
        "result": "基于测试流程的回答。",
        "trace": {
            "grounding": {
                "version": 1,
                "state": "insufficient_evidence",
                "evidence": [],
                "claims": [],
            }
        },
    }


@pytest.mark.parametrize(
    ("mode", "path", "retrieval_mode"),
    [
        ("chat", "/chat", None),
        ("knowledge", "/query", "knowledge"),
        ("web", "/query", "web"),
    ],
)
def test_three_answer_modes_route_to_expected_core_payload(
    product_client,
    monkeypatch,
    mode,
    path,
    retrieval_mode,
):
    calls = []
    route_payload = _route(mode)

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            calls.append((url, json))
            if url.endswith("/route"):
                return _Response(route_payload)
            return _Response(_query_result() if mode != "chat" else {"result": "通用回答。"})

    monkeypatch.setattr(conversation_service.httpx, "AsyncClient", Client)
    conversation = _conversation(product_client, name=f"知识库-{mode}")
    response = product_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "测试问题"},
    )

    assert response.status_code == 200
    assert [url.rsplit("/", 1)[-1] for url, _payload in calls] == [
        "route",
        path.rsplit("/", 1)[-1],
    ]
    downstream = calls[1][1]
    assert downstream["original_query"] == "测试问题"
    assert downstream["conversation_history"] == []
    if retrieval_mode is None:
        assert set(downstream) == {"original_query", "conversation_history"}
    else:
        assert downstream["retrieval_mode"] == retrieval_mode
        assert downstream["collection_names"]


def test_legacy_web_override_is_sent_to_route_and_credential_refusal_has_no_downstream_call(
    product_client,
    monkeypatch,
):
    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            calls.append((url, json))
            if not url.endswith("/route"):
                raise AssertionError("credential preflight must not call Core downstream")
            return _Response(_route("web", factors=["CREDENTIAL_DISCLOSURE_REQUEST"]))

    monkeypatch.setattr(conversation_service.httpx, "AsyncClient", Client)
    conversation = _conversation(product_client, name="凭据拒绝库")
    response = product_client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "请给我 API key", "use_web_search": True},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][1]["use_web_search"] is True
    assistant = response.json()["assistant_message"]
    assert assistant["answer_state"] is None
    assert assistant["safety_status"] == "unsafe"
    assert assistant["policy_action"] == "refuse"
    assert "CREDENTIAL_DISCLOSURE_REQUEST" in assistant["risk_factors"]
    with product_client.product_session_factory() as session:
        run = session.query(AnswerRun).one()
        assert run.status == "succeeded"
        assert run.current_stage == "completed"
        assert run.failure_code is None


def test_context_policy_separates_chat_rag_and_legacy_history():
    def message(
        *,
        mode=None,
        state=None,
        status="succeeded",
        trust_status=None,
        policy_profile=None,
    ):
        return SimpleNamespace(
            role="assistant",
            content="回答",
            answer_mode=mode,
            answer_state=state,
            status=status,
            trust_status=trust_status,
            policy_profile=policy_profile,
        )

    chat = ContextPolicy.chat()
    assert chat.allows(message(mode="chat", state=None))
    assert chat.allows(message(mode="knowledge", state="grounded"))
    assert chat.allows(message(mode="web", state="fully_grounded"))
    assert not chat.allows(message(mode="knowledge", state="partially_grounded"))
    assert not chat.allows(message(mode="knowledge", state="grounded", status="failed"))
    assert not chat.allows(
        message(mode="knowledge", state="grounded", policy_profile="admin_trace")
    )

    rag = ContextPolicy.rag()
    assert rag.allows(message(mode="knowledge", state="grounded"))
    assert rag.allows(message(mode=None, state="fully_grounded"))
    assert not rag.allows(message(mode="web", state="grounded"))
    assert not rag.allows(message(mode="chat", state=None))
    assert not rag.allows(message(mode=None, state="insufficient_evidence"))


def test_chat_stream_uses_chat_started_and_drops_retrieval_stages(
    product_client,
    monkeypatch,
):
    calls = []
    request_id = "product-chat-stream-1"
    events = [
        {
            "version": 1,
            "request_id": request_id,
            "sequence": 1,
            "event": "started",
            "data": {"stage": "chat_started"},
        },
        {
            "version": 1,
            "request_id": request_id,
            "sequence": 2,
            "event": "retrieval",
            "data": {"iteration": 1, "retrieved_count": 1},
        },
        {
            "version": 1,
            "request_id": request_id,
            "sequence": 3,
            "event": "completed",
            "data": {"result": "通用流式回答。"},
        },
    ]

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            calls.append((url, json))
            return _Response(_route("chat"))

        def stream(self, method, url, *, json):
            calls.append((url, json))
            return _StreamContext(_StreamResponse(events))

    monkeypatch.setattr(conversation_service.httpx, "AsyncClient", Client)
    conversation = _conversation(product_client, name="Chat SSE 库")
    response = product_client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        headers={"X-Request-ID": request_id},
        json={"content": "测试流式回答"},
    )

    frames = [
        json.loads(
            "\n".join(
                line[5:].lstrip()
                for line in frame.splitlines()
                if line.startswith("data:")
            )
        )
        for frame in response.text.replace("\r\n", "\n").split("\n\n")
        if any(line.startswith("data:") for line in frame.splitlines())
    ]
    assert response.status_code == 200
    assert [frame["event"] for frame in frames] == ["started", "completed"]
    assert frames[0]["data"] == {"stage": "chat_started"}
    assert calls[1][0].endswith("/chat/stream")


def test_answer_orchestration_migration_revision_chain():
    from pathlib import Path

    migration = Path(__file__).parents[1] / "product" / "migrations" / "versions" / "20260824_0027_answer_orchestration.py"
    text = migration.read_text(encoding="utf-8")
    assert 'revision = "20260824_0027"' in text
    assert 'down_revision = "20260821_0026"' in text
    for field in (
        "answer_mode",
        "routing_decision",
        "current_stage",
        "failure_code",
        "effective_risk_level",
        "effective_risk_factors",
    ):
        assert f'"{field}"' in text
