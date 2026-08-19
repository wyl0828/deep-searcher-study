from __future__ import annotations

import os


def resolve_api_key(kwargs: dict, *, provider_env: str) -> str | None:
    """Resolve candidate API key: inline api_key > api_key_env > provider default env.

    If api_key_env names an unset env var, raise instead of silently rebinding.
    """
    if "api_key" in kwargs:
        kwargs.pop("api_key_env", None)
        return kwargs.pop("api_key")
    env_name = kwargs.pop("api_key_env", None)
    if env_name:
        value = os.getenv(env_name)
        if not value:
            raise ValueError(
                f"candidate api_key_env='{env_name}' set but environment variable is missing"
            )
        return value
    return os.getenv(provider_env)


def resolve_base_url(
    kwargs: dict,
    *,
    provider_env: str | None = None,
    default: str | None = None,
) -> str | None:
    """Resolve candidate base URL: inline base_url > base_url_env > provider env > default."""
    if "base_url" in kwargs:
        kwargs.pop("base_url_env", None)
        return kwargs.pop("base_url")
    env_name = kwargs.pop("base_url_env", None)
    if env_name:
        value = os.getenv(env_name)
        if not value:
            raise ValueError(
                f"candidate base_url_env='{env_name}' set but environment variable is missing"
            )
        return value
    if provider_env:
        value = os.getenv(provider_env)
        if value:
            return value
    return default
