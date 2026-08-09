from contextlib import contextmanager

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from frontend.product.auth import ensure_legacy_owner
from frontend.product.db import Base, create_database_engine, get_session
from frontend.product.models import Conversation, KnowledgeBase
from frontend.server import app


def dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()

    def visit(dependant):
        call = getattr(dependant, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        for child in dependant.dependencies:
            visit(child)

    visit(route.dependant)
    return names


@contextmanager
def auth_client(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'auth.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        client.auth_session_factory = session_factory
        yield client
    finally:
        app.dependency_overrides.clear()


def setup_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/setup",
        json={
            "username": "owner",
            "password": "correct-horse-battery",
            "display_name": "工作台管理员",
        },
    )
    assert response.status_code == 201


def test_all_workspace_data_and_operational_routes_require_authentication():
    protected_prefixes = (
        "/api/knowledge-bases",
        "/api/documents",
        "/api/ingest-jobs",
        "/api/conversations",
        "/api/admin",
    )
    protected_exact = {
        "/api/auth/me",
        "/api/health/diagnostics",
        "/api/ingest",
        "/api/query",
    }
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    protected_routes = [
        route
        for route in routes
        if route.path in protected_exact or route.path.startswith(protected_prefixes)
    ]
    assert {route.path for route in protected_routes} >= protected_exact
    for route in protected_routes:
        assert dependency_names(route) & {"require_user", "require_admin"}, route.path


def test_first_run_setup_login_logout_and_session_cookie(tmp_path):
    with auth_client(tmp_path) as client:
        status = client.get("/api/auth/status")
        assert status.json() == {
            "setup_required": True,
            "authenticated": False,
            "user": None,
        }
        assert client.get("/api/knowledge-bases").status_code == 401

        with client.auth_session_factory() as session:
            ensure_legacy_owner(session)
            legacy_kb = KnowledgeBase(
                name="升级前资料",
                description="待首位管理员接管",
                collection_name="legacy_auth_test",
                is_current=True,
            )
            legacy_conversation = Conversation(
                knowledge_base=legacy_kb,
                title="升级前对话",
            )
            session.add_all([legacy_kb, legacy_conversation])
            session.commit()

        setup = client.post(
            "/api/auth/setup",
            json={
                "username": "Owner",
                "password": "correct-horse-battery",
                "display_name": "工作台管理员",
            },
        )
        assert setup.status_code == 201
        assert setup.json()["user"]["username"] == "owner"
        assert setup.json()["user"]["role"] == "admin"
        cookie = setup.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert [item["name"] for item in client.get("/api/knowledge-bases").json()["items"]] == [
            "升级前资料"
        ]
        assert [item["title"] for item in client.get("/api/conversations").json()["items"]] == [
            "升级前对话"
        ]

        assert client.get("/api/auth/status").json()["authenticated"] is True
        assert (
            client.post(
                "/api/auth/setup",
                json={
                    "username": "other-admin",
                    "password": "another-secure-password",
                    "display_name": "另一个管理员",
                },
            ).status_code
            == 409
        )

        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        wrong = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "wrong-password"},
        )
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"

        login = client.post(
            "/api/auth/login",
            json={"username": "OWNER", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200
        assert client.get("/api/auth/me").json()["user"]["display_name"] == "工作台管理员"


def test_users_cannot_cross_workspace_or_use_admin_endpoints(tmp_path, monkeypatch):
    async def inspect_pdf_pages(_path):
        return 1

    monkeypatch.setattr(
        "frontend.product.services.documents.inspect_pdf_pages",
        inspect_pdf_pages,
    )
    monkeypatch.setattr(
        "frontend.product.services.documents.UPLOAD_DIR",
        tmp_path / "uploads",
    )

    with auth_client(tmp_path) as client:
        setup_admin(client)
        admin_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "共享名称", "description": "仅管理员可见"},
        ).json()
        admin_conversation = client.post(
            "/api/conversations",
            json={"knowledge_base_id": admin_kb["id"]},
        ).json()
        upload = client.post(
            f"/api/knowledge-bases/{admin_kb['id']}/documents",
            files={"file": ("admin.pdf", b"%PDF-1.7\nprivate", "application/pdf")},
        ).json()
        admin_document_id = upload["document"]["id"]
        admin_job_id = upload["job"]["id"]

        member = client.post(
            "/api/admin/users",
            json={
                "username": "member-one",
                "password": "member-secure-password",
                "display_name": "普通成员",
                "role": "member",
            },
        )
        assert member.status_code == 201
        assert len(client.get("/api/admin/users").json()["items"]) == 2

        client.post("/api/auth/logout")
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "member-one", "password": "member-secure-password"},
            ).status_code
            == 200
        )

        assert client.get("/api/knowledge-bases").json()["items"] == []
        assert client.get("/api/conversations").json()["items"] == []
        protected_paths = [
            f"/api/knowledge-bases/{admin_kb['id']}",
            f"/api/knowledge-bases/{admin_kb['id']}/documents",
            f"/api/documents/{admin_document_id}",
            f"/api/documents/{admin_document_id}/content",
            f"/api/ingest-jobs/{admin_job_id}",
            f"/api/conversations/{admin_conversation['id']}",
        ]
        for path in protected_paths:
            assert client.get(path).status_code == 404
        assert client.get("/api/admin/users").status_code == 403
        assert client.post("/api/health/diagnostics").status_code == 403
        assert (
            client.post(
                "/api/query",
                json={
                    "question": "尝试绕过产品数据隔离",
                    "collection_name": "deepsearcher",
                    "max_iter": 1,
                },
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/admin/users",
                json={
                    "username": "forbidden",
                    "password": "forbidden-password",
                    "display_name": "无权限",
                    "role": "member",
                },
            ).status_code
            == 403
        )

        member_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "共享名称", "description": "成员自己的同名知识库"},
        )
        assert member_kb.status_code == 201
        assert member_kb.json()["id"] != admin_kb["id"]
        assert (
            client.post(
                "/api/conversations",
                json={"knowledge_base_id": admin_kb["id"]},
            ).status_code
            == 404
        )
