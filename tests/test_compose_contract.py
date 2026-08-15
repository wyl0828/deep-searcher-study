from pathlib import Path

import yaml


def test_compose_launcher_rejects_placeholder_provider_credentials():
    script = (ROOT / "scripts" / "compose-environment.ps1").read_text(encoding="utf-8")
    assert "Import-ProviderEnvironment" in script
    assert '"DEEPSEEK_API_KEY", "OPENAI_API_KEY"' in script
    assert "示例占位符" in script


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
        "rocketmq-permissions",
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
        "shared-ingest-tmp",
    }.issubset(compose["volumes"])


def test_app_containers_share_the_ingest_materialization_directory():
    compose = _load("compose.yaml")
    assert compose["x-app-environment"]["TMPDIR"] == "/app/shared-tmp"
    assert "shared-ingest-tmp:/app/shared-tmp" in compose["x-app-service"]["volumes"]


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
    for name in ("product-api-a", "product-api-b", "consumer-a", "consumer-b"):
        dependency = compose["services"][name]["depends_on"]["migrate"]
        assert dependency["condition"] == "service_completed_successfully"


def test_two_api_summary_mode_requires_shared_redis_lock():
    environment = _load("compose.yaml")["x-app-environment"]
    assert environment["DEEPSEARCHER_API_INSTANCES"] == 2
    assert environment["DEEPSEARCHER_SUMMARY_LOCK"] == "redis"
    assert environment["DEEPSEARCHER_REDIS_URL"] == "redis://redis:6379/0"


def test_server_override_binds_only_loopback_ports_and_no_middleware_ports():
    override = _load("compose.server.yaml")
    ported = {
        name: service.get("ports", [])
        for name, service in override["services"].items()
        if service.get("ports")
    }
    assert set(ported) == {"core-api", "product-api-a", "product-api-b"}
    published = [port for ports in ported.values() for port in ports]
    assert published
    assert all(str(port).startswith("127.0.0.1:") for port in published)


def test_server_override_configures_log_rotation_on_every_service():
    override = _load("compose.server.yaml")
    assert set(override["services"]) == set(_load("compose.yaml")["services"])
    for name, service in override["services"].items():
        options = service["logging"]["options"]
        assert options["max-size"] == "10m", name
        assert options["max-file"] == "3", name


def test_server_override_configures_cpu_limits_on_every_service():
    override = _load("compose.server.yaml")
    for name, service in override["services"].items():
        assert float(service["cpus"]) > 0, name


def test_server_override_gives_consumers_a_healthcheck():
    override = _load("compose.server.yaml")
    for name in ("consumer-a", "consumer-b"):
        assert override["services"][name]["healthcheck"]["test"]


def test_server_env_example_contains_required_keys_and_loopback_ports():
    text = (ROOT / ".env.server.example").read_text(encoding="utf-8")
    for key in (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "DEEPSEARCHER_SERVICE_TOKEN",
        "DEEPSEARCHER_ADMIN_TOKEN",
        "DEEPSEARCHER_SESSION_SECRET",
        "DEEPSEARCHER_S3_BUCKET",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ):
        assert key in text, key
    assert "PRODUCT_API_A_SERVER_PORT=18700" in text
    assert "PRODUCT_API_B_SERVER_PORT=18701" in text


def test_server_env_file_is_gitignored():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env.server" in text