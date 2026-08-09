from __future__ import annotations

import json

from evaluation.quality_gate import DEFAULT_MANIFEST, ROOT, validate_quality_gate


def test_committed_quality_gate_reports_are_consistent():
    summary = validate_quality_gate()

    assert summary["passed"], summary["errors"]
    assert summary["dataset"]["version"] == "2.2.0"
    assert summary["reports"]["retrieval"]["rows"] == 216
    assert summary["reports"]["context"]["rows"] == 36
    assert summary["reports"]["answer"]["rows"] == 72


def test_quality_gate_rejects_an_impossible_threshold(tmp_path):
    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    manifest["reports"]["retrieval"]["thresholds"].append(
        {
            "path": "metrics.dense.retrieval_hit_rate",
            "op": "ge",
            "value": 1.1,
        }
    )
    manifest_path = tmp_path / "quality_gate.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    summary = validate_quality_gate(manifest_path=manifest_path, project_root=ROOT)

    assert not summary["passed"]
    assert any("retrieval_hit_rate" in error for error in summary["errors"])
