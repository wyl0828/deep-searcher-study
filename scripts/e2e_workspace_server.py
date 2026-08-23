"""Start a deterministic product workspace for browser end-to-end tests."""

from __future__ import annotations

import json
import os
import shutil
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
        Document,
        KnowledgeBase,
        Message,
        User,
        Workspace,
        WorkspaceMember,
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
        session.add(user)
        session.flush()
        workspace = Workspace(
            id="ws_e2e",
            name="E2E 工作区",
            description="E2E 自动验收",
            owner_id=user.id,
        )
        session.add(workspace)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role="owner",
            )
        )
        knowledge_base = KnowledgeBase(
            id="kb_e2e_grounding",
            owner_id=user.id,
            workspace_id=workspace.id,
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
        media_conversation = Conversation(
            id="conv_e2e_media",
            knowledge_base=knowledge_base,
            owner=user,
            title="媒体引用自动验收",
        )
        media_user_message = Message(
            id="msg_e2e_media_user",
            conversation=media_conversation,
            role="user",
            content="请验证图片引用和大量来源不会破坏聊天布局。",
            status="succeeded",
        )
        media_assistant_message = Message(
            id="msg_e2e_media_assistant",
            conversation=media_conversation,
            role="assistant",
            content="图片引用应保持为受控预览，不应撑开聊天流。\n\n![测试原文图片](/deepsearcher-badge.png)",
            status="succeeded",
            answer_state="grounded",
        )
        media_upload_dir = E2E_ROOT / "data" / "uploads" / knowledge_base.id
        media_upload_dir.mkdir(parents=True, exist_ok=True)
        media_pdf_path = media_upload_dir / "e2e-media.pdf"
        shutil.copyfile(ROOT / "examples" / "data" / "WhatisMilvus.pdf", media_pdf_path)
        media_document = Document(
            id="doc_e2e_media",
            knowledge_base=knowledge_base,
            display_name="media-evidence.pdf",
            storage_path=str(media_pdf_path),
            storage_type="local",
            size_bytes=1,
            page_count=27,
            sha256="0" * 64,
            status="ready",
        )
        media_citations = [
            Citation(
                id=f"citation_e2e_media_{index}",
                message=media_assistant_message,
                document_id=media_document.id if index == 1 else None,
                index=index,
                display_name="media-evidence.pdf" if index == 1 else f"source-{index}.pdf",
                page_number=index,
                text=f"第 {index} 条测试证据，用于验证默认只展示前五条来源。",
                supported=True,
            )
            for index in range(1, 28)
        ]
        session.add_all(
            [
                user,
                knowledge_base,
                conversation,
                user_message,
                assistant_message,
                citation,
                *claims,
                media_conversation,
                media_user_message,
                media_assistant_message,
                media_document,
                *media_citations,
            ]
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
