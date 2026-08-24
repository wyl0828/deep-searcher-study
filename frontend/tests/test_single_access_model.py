from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import select

from frontend.tests.test_auth import auth_client, setup_admin
from frontend.product.models import UserKnowledgeBaseAccess


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def test_department_company_and_direct_access_are_the_only_runtime_sources(tmp_path):
    with auth_client(tmp_path) as client:
        setup_admin(client)
        department = client.post("/api/admin/departments", json={"name": "研发部"})
        assert department.status_code == 201, department.text
        department_id = department.json()["department"]["id"]

        hyd = client.post(
            "/api/admin/users",
            json={
                "username": "hyd",
                "password": "hyd-test-password",
                "display_name": "普通用户",
                "role": "member",
                "department_id": department_id,
            },
        )
        assert hyd.status_code == 201, hyd.text
        hyd_id = hyd.json()["user"]["id"]

        company = client.post(
            "/api/knowledge-bases",
            json={"name": "全员资料", "description": "company"},
        )
        department_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "研发资料", "description": "department"},
        )
        direct_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "专项资料", "description": "direct"},
        )
        other_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "财务资料", "description": "other"},
        )
        assert all(response.status_code == 201 for response in (company, department_kb, direct_kb, other_kb))
        company_id = company.json()["id"]
        department_kb_id = department_kb.json()["id"]
        direct_kb_id = direct_kb.json()["id"]
        other_kb_id = other_kb.json()["id"]

        assert client.put(
            "/api/admin/knowledge-access/company-wide",
            json={"knowledge_base_ids": [company_id]},
        ).status_code == 200
        assert client.put(
            f"/api/admin/departments/{department_id}/knowledge-access",
            json={"knowledge_base_ids": [department_kb_id]},
        ).status_code == 200
        assert client.put(
            f"/api/admin/users/{hyd_id}/knowledge-access",
            json={"knowledge_base_ids": [direct_kb_id]},
        ).status_code == 200

        client.post("/api/auth/logout")
        login(client, "hyd", "hyd-test-password")
        accessible = {item["id"] for item in client.get("/api/knowledge-bases").json()["items"]}
        assert accessible == {company_id, department_kb_id, direct_kb_id}
        assert other_kb_id not in accessible
        assert client.get(f"/api/knowledge-bases/{other_kb_id}").status_code == 404

        client.post("/api/auth/logout")
        login(client, "owner", "correct-horse-battery")
        assert other_kb_id in {item["id"] for item in client.get("/api/knowledge-bases").json()["items"]}


def test_last_admin_cannot_be_disabled_or_demoted(tmp_path):
    with auth_client(tmp_path) as client:
        setup_admin(client)
        admin = client.get("/api/auth/me").json()["user"]
        assert client.patch(
            f"/api/admin/users/{admin['id']}",
            json={"is_active": False},
        ).status_code == 409
        assert client.patch(
            f"/api/admin/users/{admin['id']}",
            json={"role": "member"},
        ).status_code == 409


def test_user_access_classifies_overlapping_direct_grants_as_inherited(tmp_path):
    with auth_client(tmp_path) as client:
        setup_admin(client)
        department = client.post("/api/admin/departments", json={"name": "交叉授权部"}).json()["department"]
        member = client.post(
            "/api/admin/users",
            json={
                "username": "overlap-member",
                "password": "overlap-password",
                "display_name": "重叠成员",
                "role": "member",
                "department_id": department["id"],
            },
        ).json()["user"]
        inherited = client.post("/api/knowledge-bases", json={"name": "继承资料"}).json()
        extra = client.post("/api/knowledge-bases", json={"name": "额外资料"}).json()

        assert client.put(
            "/api/admin/knowledge-access/company-wide",
            json={"knowledge_base_ids": [inherited["id"]]},
        ).status_code == 200
        assert client.put(
            f"/api/admin/departments/{department['id']}/knowledge-access",
            json={"knowledge_base_ids": [inherited["id"]]},
        ).status_code == 200
        assert client.put(
            f"/api/admin/users/{member['id']}/knowledge-access",
            json={"knowledge_base_ids": [inherited["id"], extra["id"]]},
        ).status_code == 200

        access = client.get(f"/api/admin/users/{member['id']}/knowledge-access")
        assert access.status_code == 200
        payload = access.json()
        assert payload["inherited_access"] == [{
            "knowledge_base_id": inherited["id"],
            "knowledge_base_name": "继承资料",
            "sources": ["company_wide", "department"],
        }]
        assert payload["personal_extra_access"] == [{
            "knowledge_base_id": extra["id"],
            "knowledge_base_name": "额外资料",
        }]
        assert payload["effective_access_count"] == 2

        saved = client.put(
            f"/api/admin/users/{member['id']}/knowledge-access",
            json={"knowledge_base_ids": [inherited["id"], extra["id"]]},
        )
        assert saved.status_code == 200
        assert saved.json()["personal_extra_access"] == [{
            "knowledge_base_id": extra["id"],
            "knowledge_base_name": "额外资料",
        }]

        with client.auth_session_factory() as session:
            rows = session.scalars(
                select(UserKnowledgeBaseAccess.knowledge_base_id).where(
                    UserKnowledgeBaseAccess.user_id == member["id"]
                )
            ).all()
        assert rows == [extra["id"]]


def test_knowledge_access_summary_counts_only_active_ordinary_users(tmp_path):
    with auth_client(tmp_path) as client:
        setup_admin(client)
        active = client.post(
            "/api/admin/users",
            json={"username": "summary-active", "password": "summary-password", "display_name": "正常成员", "role": "member"},
        ).json()["user"]
        disabled = client.post(
            "/api/admin/users",
            json={"username": "summary-disabled", "password": "summary-password", "display_name": "停用成员", "role": "member"},
        ).json()["user"]
        assert client.patch(f"/api/admin/users/{disabled['id']}", json={"is_active": False}).status_code == 200
        knowledge_base = client.post("/api/knowledge-bases", json={"name": "覆盖统计资料"}).json()
        assert client.put(
            "/api/admin/knowledge-access/company-wide",
            json={"knowledge_base_ids": [knowledge_base["id"]]},
        ).status_code == 200
        summary = client.get(f"/api/admin/knowledge-bases/{knowledge_base['id']}/access-summary")
        assert summary.status_code == 200
        assert summary.json()["accessible_user_count"] == 1
        assert "covered_user_count" not in summary.json()
        assert active["id"] not in {item["id"] for item in summary.json()["direct_users"]}
