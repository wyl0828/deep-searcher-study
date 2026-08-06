import sys
from unittest.mock import Mock

import pytest

from deepsearcher import cli


def prepare_cli(monkeypatch, arguments):
    load_local = Mock(return_value={"active_collection": "kb_safe"})
    load_website = Mock(return_value={"active_collection": "kb_safe"})
    monkeypatch.setattr(sys, "argv", ["deepsearcher", *arguments])
    monkeypatch.setattr(cli, "Configuration", Mock(return_value=object()))
    monkeypatch.setattr(cli, "init_config", Mock())
    monkeypatch.setattr(cli, "load_from_local_files", load_local)
    monkeypatch.setattr(cli, "load_from_website", load_website)
    return load_local, load_website


def test_force_new_collection_is_an_explicit_boolean_switch(monkeypatch):
    load_local, _load_website = prepare_cli(
        monkeypatch,
        [
            "load",
            "guide.pdf",
            "--collection_name",
            "kb_safe",
            "--force-new-collection",
        ],
    )

    cli.main()

    load_local.assert_called_once_with(
        ["guide.pdf"],
        collection_name="kb_safe",
        force_new_collection=True,
        batch_size=256,
    )


def test_force_new_collection_is_omitted_when_switch_is_absent(monkeypatch):
    load_local, _load_website = prepare_cli(
        monkeypatch,
        ["load", "guide.pdf"],
    )

    cli.main()

    assert "force_new_collection" not in load_local.call_args.kwargs


def test_force_rebuild_rejects_mixed_local_and_url_inputs(monkeypatch):
    load_local, load_website = prepare_cli(
        monkeypatch,
        [
            "load",
            "guide.pdf",
            "https://example.com",
            "--force-new-collection",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    load_local.assert_not_called()
    load_website.assert_not_called()
