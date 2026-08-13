from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontend.product.models import Base, Conversation, ConversationSummary, Message
from frontend.product.repositories import create_knowledge_base
from frontend.product.services import conversation_summaries as summaries


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'summaries.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_conversation(factory, *, turns=4, weak_last=False):
    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="摘要库", description="")
        conversation = Conversation(
            knowledge_base_id=knowledge_base.id,
            owner_id=knowledge_base.owner_id,
        )
        session.add(conversation)
        session.flush()
        for index in range(turns):
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=f"问题 {index}",
                    status="succeeded",
                )
            )
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=f"回答 {index} [E{index + 1}]",
                    status="succeeded",
                    answer_state=(
                        "insufficient_evidence"
                        if weak_last and index == turns - 1
                        else "fully_grounded"
                    ),
                )
            )
        session.commit()
        return conversation.id


@pytest.fixture(autouse=True)
def summary_settings(monkeypatch):
    monkeypatch.setattr(summaries, "SUMMARY_ENABLED", True)
    monkeypatch.setattr(summaries, "SUMMARY_START_TURNS", 4)
    monkeypatch.setattr(summaries, "SUMMARY_KEEP_TURNS", 2)
    monkeypatch.setattr(summaries, "SUMMARY_MAX_CHARS", 200)
    monkeypatch.setattr(summaries, "SUMMARY_LOCK_MODE", "local")


def test_summary_persists_cutoff_model_and_prompt_version(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    conversation_id = add_conversation(factory)
    captured = {}

    def generate(payload):
        captured.update(payload)
        return "用户讨论了多个问题", "summary-model"

    monkeypatch.setattr(summaries, "generate_summary", generate)
    with factory() as session:
        record = summaries.summarize_if_needed(session, session.get(Conversation, conversation_id))

    assert record is not None
    assert record.model == "summary-model"
    assert record.prompt_version == summaries.SUMMARY_PROMPT_VERSION
    assert record.last_message_id
    assert "[E" not in str(captured)


def test_history_combines_latest_summary_and_recent_trusted_messages(tmp_path):
    factory = make_session(tmp_path)
    conversation_id = add_conversation(factory, turns=5, weak_last=True)
    with factory() as session:
        conversation = session.get(Conversation, conversation_id)
        cutoff = conversation.messages[3]
        session.add(
            ConversationSummary(
                conversation_id=conversation_id,
                content="旧话题摘要",
                last_message_id=cutoff.id,
                model="summary-model",
                prompt_version=summaries.SUMMARY_PROMPT_VERSION,
            )
        )
        session.commit()
        history = summaries.build_summary_aware_history(session, conversation)

    assert history[0]["role"] == "system"
    assert "旧话题摘要" in history[0]["content"]
    assert all("[E" not in item["content"] for item in history)
    assert "回答 4" not in {item["content"] for item in history}


def test_summary_failure_keeps_existing_summary(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    conversation_id = add_conversation(factory)
    monkeypatch.setattr(
        summaries,
        "generate_summary",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )
    with factory() as session:
        before = session.query(ConversationSummary).count()
        with pytest.raises(RuntimeError, match="provider failed"):
            summaries.summarize_if_needed(session, session.get(Conversation, conversation_id))
        assert session.query(ConversationSummary).count() == before


def test_unacquired_redis_lock_skips_summary_without_blocking(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    conversation_id = add_conversation(factory)

    class FakeLock:
        def acquire(self, **_kwargs):
            return False

    class FakeRedis:
        def lock(self, *_args, **_kwargs):
            return FakeLock()

    monkeypatch.setattr(summaries, "SUMMARY_LOCK_MODE", "redis")
    monkeypatch.setenv("DEEPSEARCHER_REDIS_URL", "redis://example")
    monkeypatch.setitem(
        __import__("sys").modules,
        "redis",
        SimpleNamespace(
            Redis=SimpleNamespace(from_url=lambda _url: FakeRedis()),
            exceptions=SimpleNamespace(LockError=RuntimeError),
        ),
    )
    with factory() as session:
        assert (
            summaries.summarize_if_needed(session, session.get(Conversation, conversation_id))
            is None
        )


def test_multi_api_summary_requires_redis_lock(monkeypatch):
    monkeypatch.setenv("DEEPSEARCHER_API_INSTANCES", "2")
    monkeypatch.setattr(summaries, "SUMMARY_LOCK_MODE", "local")

    with pytest.raises(summaries.SummaryConfigurationError, match="Redis summary lock"):
        summaries.validate_summary_configuration()
