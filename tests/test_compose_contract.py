from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    with (ROOT / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def test_engineering_topology_contains_expected_services_and_persistence():
    compose = _load("compose.yaml")
    expected = {
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "rocketmq-namesrv",
        "rocketmq-broker",
        "rocketmq-init",
        "etcd",
        "milvus",
        "migrate",
        "core-api",
        "product-api-a",
        "product-api-b",
        "consumer-a",
        "consumer-b",
    }
    assert set(compose["services"]) == expected
    assert {
        "postgres-data",
        "redis-data",
        "minio-data",
        "rocketmq-store",
        "etcd-data",
        "milvus-data",
    }.issubset(compose["volumes"])


def test_base_topology_does_not_publish_host_ports():
    compose = _load("compose.yaml")
    assert all("ports" not in service for service in compose["services"].values())


def test_local_override_only_binds_loopback_ports():
    override = _load("compose.local.yaml")
    published = [
        port
        for service in override["services"].values()
        for port in service.get("ports", [])
    ]
    assert published
    assert all(str(port).startswith("127.0.0.1:") for port in published)


def test_postgres_migration_precedes_product_processes():
    compose = _load("compose.yaml")
    migrate = compose["services"]["migrate"]
    assert migrate["command"][-3:] == ["alembic", "upgrade", "head"]
    assert "--no-dev" in migrate["command"]
    for name in ("product-api-a", "product-api-b", "consumer-a", "consumer-b"):
        dependency = compose["services"][name]["depends_on"]["migrate"]
        assert dependency["condition"] == "service_completed_successfully"


def test_two_api_summary_mode_requires_shared_redis_lock():
    environment = _load("compose.yaml")["x-app-environment"]
    assert environment["DEEPSEARCHER_API_INSTANCES"] == 2
    assert environment["DEEPSEARCHER_SUMMARY_LOCK"] == "redis"
    assert environment["DEEPSEARCHER_REDIS_URL"] == "redis://redis:6379/0"
