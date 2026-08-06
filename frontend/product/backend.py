from __future__ import annotations

import os
import re

REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def backend_request_headers(request_id: str | None = None) -> dict[str, str]:
    """Return trusted service-to-service tenant headers for DeepSearcher API calls."""
    tenant_id = (
        (
            os.environ.get("DEEPSEARCHER_PRODUCT_TENANT")
            or os.environ.get("DEEPSEARCHER_DEFAULT_TENANT")
            or "local"
        )
        .strip()
        .lower()
    )
    headers = {"X-DeepSearcher-Tenant": tenant_id}
    service_token = os.environ.get("DEEPSEARCHER_SERVICE_TOKEN")
    if service_token:
        headers["X-DeepSearcher-Service-Token"] = service_token
    if request_id and REQUEST_ID_PATTERN.fullmatch(request_id):
        headers["X-Request-ID"] = request_id
    return headers
