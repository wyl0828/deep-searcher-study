"""Dashboard service tests (pure helpers + DB aggregation)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from frontend.product.db import Base, create_database_engine
from frontend.product.models import (
    ConnectorSync,
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgeHealthSnapshot,
    Message,
    MessageFeedback,
    User,
    Workspace,
    WorkspaceMember,
)
from frontend.product.services.dashboard import (
    _daily_series,
    _kpi,
    overview,
    trends,
)

NOW = datetime(2026, 8, 18, 4, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as store:
        yield store
    engine.dispose()


def _kb(session: Session) -> KnowledgeBase:
    user = User(
        username=f"owner{uuid4().hex[:6]}",
        display_name="O",
        password_hash="x" * 60,
        role="admin",
    )
    session.add(user)
    session.flush()
    suffix = uuid4().hex[:6]
    workspace = Workspace(
        name=f"ws{suffix}", description="", owner_id=user.id
    )
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    knowledge_base = KnowledgeBase(
        owner_id=user.id,
        workspace_id=workspace.id,
        name="kb",
        description="",
        collection_name=f"coll{suffix}",
        is_current=True,
    )
    session.add(knowledge_base)
    session.flush()
    return knowledge_base


# ---- pure helpers ----


def test_daily_series_fixed_window_zero_fill_same_day_merge():
    points = _daily_series(
        [
            datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 18, 3, 0, 0, tzinfo=timezone.utc),
        ],
        days=7,
        now=NOW,
    )
    assert len(points) == 7
    assert points[0] == {"ts": "2026-08-12", "value": 1}
    assert points[1] == {"ts": "2026-08-13", "value": 0}  # zero-fill
    assert points[-1] == {"ts": "2026-08-18", "value": 2}  # same-day merge + today


def test_daily_series_excludes_earlier_than_start():
    points = _daily_series(
        [datetime(2026, 8, 11, 23, 59, 0, tzinfo=timezone.utc)],  # before window start
        days=7,
        now=NOW,
    )
    assert len(points) == 7
    assert all(point["value"] == 0 for point in points)


def test_daily_series_30_days():
    points = _daily_series([], days=30, now=NOW)
    assert len(points) == 30


def test_kpi_delta_pct_and_null_base():
    assert _kpi(110, delta=10, base=100)["delta_pct"] == 10.0
    assert _kpi(1, delta=1, base=0)["delta_pct"] is None
    assert _kpi(5)["delta"] is None and _kpi(5)["delta_pct"] is None


# ---- overview ----


def test_overview_empty_db_all_zero(session):
    result = overview(session)
    assert result["kpis"]["users"]["value"] == 0
    assert result["kpis"]["knowledge_bases"]["value"] == 0
    assert result["kpis"]["documents"]["value"] == 0
    distribution = result["health_distribution"]
    assert sum(distribution.values()) == 0


def test_overview_aggregates_counts(session):
    current = _kb(session)
    user = User(username="u1", display_name="U1", password_hash="x" * 60, role="member")
    session.add(user)
    session.flush()
    document = Document(
        knowledge_base_id=current.id,
        display_name="a.txt",
        storage_path="x",
        size_bytes=1,
        sha256="a" * 64,
        status="ready",
    )
    conversation = Conversation(owner_id=user.id, knowledge_base_id=current.id)
    session.add_all([document, conversation])
    session.flush()
    message = Message(conversation_id=conversation.id, role="assistant", content="hi", status="succeeded")
    session.add(message)
    session.flush()
    session.add(
        MessageFeedback(
            user_id=user.id,
            message_id=message.id,
            vote=-1,
            cancelled=False,
        )
    )
    sync = ConnectorSync(
        knowledge_base_id=current.id,
        source_type="local_directory",
        config={"root": "x"},
        cron="0 * * * *",
        status="active",
    )
    session.add(sync)
    session.commit()

    result = overview(session)
    assert result["kpis"]["users"]["value"] == 2
    assert result["kpis"]["knowledge_bases"]["value"] == 1
    assert result["kpis"]["documents"]["value"] == 1
    assert result["kpis"]["documents"]["ready"] == 1
    assert result["kpis"]["conversations"]["value"] == 1
    assert result["kpis"]["messages"]["value"] == 1
    assert result["kpis"]["feedback"]["value"] == 1
    assert result["kpis"]["connector_syncs"]["value"] == 1


def test_health_distribution_unknown_invariant(session):
    _kb(session)  # KB without a snapshot -> unknown
    healthy = _kb(session)
    session.add(
        KnowledgeHealthSnapshot(
            knowledge_base_id=healthy.id,
            owner_id=healthy.owner_id,
            formula_version="1.1",
            status="complete",
            overall_score=90.0,
            data_score=90.0,
            retrieval_score=90.0,
            trust_score=90.0,
            metrics={},
            deductions=[],
            actions=[],
        )
    )
    session.commit()

    distribution = overview(session)["health_distribution"]
    assert distribution["unknown"] == 1
    assert distribution["healthy"] == 1
    assert (
        sum(distribution.values())
        == overview(session)["kpis"]["knowledge_bases"]["value"]
    )


# ---- trends ----


def test_trends_zero_filled_and_bounded(session):
    result = trends(session, days=7)
    assert result["granularity"] == "daily"
    assert result["window"] == "7d"
    names = [series["name"] for series in result["series"]]
    assert names == ["documents", "messages", "feedback"]
    for series in result["series"]:
        assert len(series["data"]) == 7
        assert all(isinstance(point["value"], int) for point in series["data"])


def test_trends_buckets_same_day_and_start_inclusive(session):
    current = _kb(session)
    user = User(username="t", display_name="T", password_hash="x" * 60, role="member")
    session.add(user)
    session.flush()
    conversation = Conversation(owner_id=user.id, knowledge_base_id=current.id)
    session.add(conversation)
    session.flush()
    start = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)  # today
    session.add(Message(conversation_id=conversation.id, role="assistant", content="a", status="succeeded", created_at=start))
    session.add(Message(conversation_id=conversation.id, role="assistant", content="b", status="succeeded", created_at=start + timedelta(hours=2)))
    session.commit()

    message_series = next(s for s in trends(session, days=7)["series"] if s["name"] == "messages")
    last = message_series["data"][-1]
    assert last["ts"] == "2026-08-18"
    assert last["value"] == 2
    assert sum(point["value"] for point in message_series["data"]) == 2
