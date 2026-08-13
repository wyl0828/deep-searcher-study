from __future__ import annotations

import json

from evaluation.quality_gate import DEFAULT_MANIFEST, ROOT, validate_quality_gate


def test_release_gate_contract_uses_tracked_entailment_report_and_multidoc_threshold():
    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    threshold = next(
        item
        for item in manifest["reports"]["retrieval"]["thresholds"]
        if item["path"] == "metrics.hybrid.full_document_coverage_rate"
    )
    report = ROOT / "evaluation" / "results" / "v0.3-entailment-deepseek-20260812" / "report.json"

    assert threshold == {"path": threshold["path"], "op": "ge", "value": 0.625}
    stability_paths = {
        item["path"]
        for item in manifest["reports"]["retrieval"]["thresholds"]
        if "stability_rate" in item["path"]
    }
    assert stability_paths == {
        "metrics.hybrid.query_plan_stability_rate",
        "metrics.hybrid.retrieval_given_plan_stability_rate",
        "metrics.hybrid.end_to_end_ranking_stability_rate",
    }
    assert {
        "path": "freshness_classifier_version",
        "op": "eq",
        "value": "1.1.0",
    } in manifest["reports"]["answer"]["thresholds"]
    assert report.is_file()
    assert "_local-" not in report.as_posix()


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
