from __future__ import annotations

import pytest

from deepsearcher.llm import Aliyun, DeepSeek, OpenAI


def _client(model_cls, **kwargs):
    return model_cls("m", **kwargs).client


def test_api_key_env_reads_named_env(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-key")
    monkeypatch.setenv("OPENCODE_GO_BASE_URL", "https://opencode/zen/v1")
    client = _client(
        DeepSeek, api_key_env="OPENCODE_GO_API_KEY", base_url_env="OPENCODE_GO_BASE_URL"
    )
    assert client.api_key == "go-key"
    assert str(client.base_url).rstrip("/") == "https://opencode/zen/v1"


def test_base_url_env_reads_named_env(monkeypatch):
    monkeypatch.setenv("BAILIAN_BASE_URL", "https://maas/v1")
    monkeypatch.setenv("BAILIAN_API_KEY", "b")
    client = _client(OpenAI, api_key_env="BAILIAN_API_KEY", base_url_env="BAILIAN_BASE_URL")
    assert str(client.base_url).rstrip("/") == "https://maas/v1"


def test_inline_api_key_and_base_url_win_over_env(monkeypatch):
    monkeypatch.setenv("X_K", "env-key")
    monkeypatch.setenv("X_U", "https://env")
    client = _client(
        DeepSeek,
        api_key="inline-key",
        api_key_env="X_K",
        base_url="https://inline",
        base_url_env="X_U",
    )
    assert client.api_key == "inline-key"
    assert client.base_url == "https://inline"


def test_without_env_falls_back_to_provider_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "provider-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek")
    client = _client(DeepSeek)
    assert client.api_key == "provider-key"
    assert client.base_url == "https://deepseek"


def test_api_key_env_missing_raises(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with pytest.raises(ValueError):
        _client(DeepSeek, api_key_env="MISSING_KEY", base_url_env="MISSING_URL")


def test_two_deepseek_candidates_keep_separate_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_OFFICIAL_KEY", "official")
    monkeypatch.setenv("DEEPSEEK_OFFICIAL_URL", "https://official")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "opencode")
    monkeypatch.setenv("OPENCODE_GO_BASE_URL", "https://opencode")
    official = _client(
        DeepSeek, api_key_env="DEEPSEEK_OFFICIAL_KEY", base_url_env="DEEPSEEK_OFFICIAL_URL"
    )
    opencode = _client(
        DeepSeek, api_key_env="OPENCODE_GO_API_KEY", base_url_env="OPENCODE_GO_BASE_URL"
    )
    assert (official.api_key, official.base_url) == ("official", "https://official")
    assert (opencode.api_key, opencode.base_url) == ("opencode", "https://opencode")
