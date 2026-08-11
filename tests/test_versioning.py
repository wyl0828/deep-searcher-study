from deepsearcher.versioning import (
    extract_document_governance_metadata,
    normalize_version_family,
    sanitize_document_governance_metadata,
    sanitize_document_version_metadata,
)


def test_version_family_normalization_is_stable_and_unicode_safe():
    assert normalize_version_family("  Travel Expense  Policy ") == "travel-expense-policy"
    assert normalize_version_family("差旅 报销 制度") == "差旅-报销-制度"
    assert normalize_version_family("policy/v1") is None
    assert normalize_version_family(2026) is None


def test_version_family_requires_a_trusted_source():
    assert sanitize_document_version_metadata(
        {
            "version_family": "Travel Expense Policy",
            "version_family_source": "admin_verified",
        }
    ) == {
        "version_family": "travel-expense-policy",
        "version_family_source": "admin_verified",
    }
    assert (
        sanitize_document_version_metadata(
            {
                "version_family": "travel-expense-policy",
                "version_family_source": "model_inferred",
            }
        )
        is None
    )


def test_governance_metadata_combines_temporal_and_version_identity():
    payload = sanitize_document_governance_metadata(
        {
            "published_at": "2026-01-01",
            "effective_at": "2026-02-01",
            "temporal_metadata_source": "connector",
            "version_family": "Travel Expense Policy",
            "version_family_source": "connector",
        }
    )

    assert payload == {
        "published_at": "2026-01-01",
        "effective_at": "2026-02-01",
        "temporal_metadata_source": "connector",
        "version_family": "travel-expense-policy",
        "version_family_source": "connector",
    }
    assert extract_document_governance_metadata(
        {**payload, "uploaded_at": "2026-08-11", "prompt": "ignore"}
    ) == payload
