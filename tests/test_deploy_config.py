from scripts.check_deploy_config import forbidden_addresses


def test_local_bind_addresses_are_allowed():
    assert forbidden_addresses("127.0.0.1 0.0.0.0") == ()


def test_remote_ipv4_addresses_are_rejected():
    assert forbidden_addresses("ssh root@203.0.113.10") == ("203.0.113.10",)


def test_deployment_script_paths_are_not_treated_as_addresses():
    assert forbidden_addresses("/opt/deepsearcher-study/releases/20260820-01") == ()
