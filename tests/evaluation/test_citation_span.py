import json
from pathlib import Path

import pytest

from evaluation.citation_span import (
    DEFAULT_DATASET,
    load_citation_span_dataset,
    main,
    run_citation_span_evaluation,
)


def test_citation_span_gold_dataset_passes_all_cases():
    report = run_citation_span_evaluation(load_citation_span_dataset())

    assert report["metrics"]["failed"] == 0
    assert report["metrics"]["accuracy"] == 1.0
    assert report["dataset"]["case_count"] >= 8


def test_citation_span_dataset_rejects_duplicate_ids(tmp_path: Path):
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case id"):
        load_citation_span_dataset(path)


def test_citation_span_cli_writes_machine_readable_report(tmp_path: Path):
    output = tmp_path / "citation-span.json"

    assert main(["--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["failed"] == 0
