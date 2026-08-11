from datetime import date, datetime, timezone

from deepsearcher.temporal import (
    extract_document_temporal_metadata,
    published_anchor,
    sanitize_document_temporal_metadata,
)


def test_document_temporal_metadata_is_canonical_and_bounded():
    value = sanitize_document_temporal_metadata(
        {
            "published_at": date(2026, 8, 1),
            "effective_at": "2026-08-05",
            "superseded_at": "2026-09-01",
            "temporal_metadata_source": "admin_verified",
        }
    )

    assert value == {
        "published_at": "2026-08-01",
        "effective_at": "2026-08-05",
        "superseded_at": "2026-09-01",
        "temporal_metadata_source": "admin_verified",
    }
    assert published_anchor(value) == date(2026, 8, 1)


def test_document_temporal_metadata_rejects_datetime_and_untrusted_fields():
    assert (
        sanitize_document_temporal_metadata(
            {
                "published_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
                "temporal_metadata_source": "user_declared",
            }
        )
        is None
    )
    assert (
        sanitize_document_temporal_metadata(
            {
                "published_at": "2026-08-01",
                "temporal_metadata_source": "model_inferred",
            }
        )
        is None
    )
    assert (
        sanitize_document_temporal_metadata(
            {
                "published_at": "2026-08-01",
                "temporal_metadata_source": "user_declared",
                "uploaded_at": "2026-08-11",
            }
        )
        is None
    )


def test_extract_temporal_metadata_ignores_file_and_ingestion_timestamps():
    assert extract_document_temporal_metadata(
        {
            "published_at": "2026-08-01",
            "temporal_metadata_source": "connector",
            "file_mtime": "2026-08-09",
            "uploaded_at": "2026-08-11",
        }
    ) == {
        "published_at": "2026-08-01",
        "temporal_metadata_source": "connector",
    }
    assert published_anchor({"file_mtime": "2026-08-09"}) is None
