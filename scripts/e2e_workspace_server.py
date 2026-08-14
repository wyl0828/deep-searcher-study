"""Start a deterministic product workspace for browser end-to-end tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E2E_ROOT = ROOT / "tmp" / "e2e-workspace"
DATABASE_PATH = E2E_ROOT / "workspace.db"


def _configure_environment() -> None:
    E2E_ROOT.mkdir(parents=True, exist_ok=True)
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
    os.environ["DEEPSEARCHER_DATABASE_URL"] = f"sqlite:///{DATABASE_PATH.as_posix()}"
    os.environ["DEEPSEARCHER_DATA_DIR"] = str(E2E_ROOT / "data")


def _seed_workspace() -> None:
    from frontend.product.auth import hash_password
    from frontend.product.db import ENGINE, Base, SessionLocal
    from frontend.product.models import (
        AnswerClaim,
        Citation,
        Conversation,
        KnowledgeBase,
        Message,
        User,
    )

    Base.metadata.create_all(ENGINE)
    with SessionLocal() as session:
        user = User(
            id="usr_e2e",
            username="e2e-user",
            display_name="E2E 用户",
            password_hash=hash_password("e2e-secure-password"),
            role="admin",
        )
        knowledge_base = KnowledgeBase(
            id="kb_e2e_grounding",
            name="E2E 证据工作台",
            description="自动验证原答案、代码块、Claim 与引用抽屉",
            collection_name="e2e_grounding_collection",
            index_manifest=json.dumps({"version": 1}),
            is_current=True,
            owner=user,
        )
        conversation = Conversation(
            id="conv_e2e_grounding",
            knowledge_base=knowledge_base,
            owner=user,
            title="Grounding 自动验收",
        )
        user_message = Message(
            id="msg_e2e_user",
            conversation=conversation,
            role="user",
            content="请保留原答案、代码块和逐条证据核验。",
            status="succeeded",
        )
        assistant_message = Message(
            id="msg_e2e_assistant",
            conversation=conversation,
            role="assistant",
            content="""原始回答正文应完整显示。 [E1]

```python
print('preserved')
```

第二条结论需要单独核验。[E9]""",
            status="succeeded",
            answer_state="partially_grounded",
        )
        evidence_text = (
            "这是传给最终模型并持久化的同一份较宽证据快照，包含被引用事实。原始回答正文应完整显示。"
        )
        citation = Citation(
            id="citation_e2e_1",
            message=assistant_message,
            index=1,
            display_name="grounding-evidence.pdf",
            page_number=2,
            chunk_index=7,
            section_title="证据快照",
            text=evidence_text,
            supported=True,
        )
        claims = [
            AnswerClaim(
                id="claim_e2e_1",
                message=assistant_message,
                index=1,
                text="原始回答正文应完整显示。",
                support_status="supported",
                citation_indices=[1],
                citation_spans=[
                    {
                        "citation_index": 1,
                        "start": evidence_text.index("原始回答正文应完整显示。"),
                        "end": len(evidence_text),
                        "text": "原始回答正文应完整显示。",
                        "match_type": "normalized_exact",
                        "score": 1.0,
                    }
                ],
            ),
            AnswerClaim(
                id="claim_e2e_2",
                message=assistant_message,
                index=2,
                text="第二条结论需要单独核验。",
                support_status="invalid_citation",
                citation_indices=[],
            ),
        ]
        session.add_all(
            [user, knowledge_base, conversation, user_message, assistant_message, citation, *claims]
        )
        session.commit()


def main() -> None:
    _configure_environment()
    _seed_workspace()
    import uvicorn

    port = int(os.environ.get("DEEPSEARCHER_E2E_PORT", "18766"))
    uvicorn.run(
        "frontend.server:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
