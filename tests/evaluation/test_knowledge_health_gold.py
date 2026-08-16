"""Gold dataset contract and formula-1.1 regression for Knowledge Health.

The gold dataset locks the frozen formula 1.1 contract without a database,
current time or model calls. Every case runs all three compute_* functions;
the expected per-dimension scores, required/forbidden deduction codes, action
codes and overall level must match the production entry points. If a compute_*
is red by the gold, treat it as a registered implementation defect - never
adjust expected to fit the implementation.
"""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

import pytest

from frontend.product.models import Document
from frontend.product.services.knowledge_health import (
    aggregate_overall_score,
    compute_data_health,
    compute_retrieval_health,
    compute_trust_health,
    get_health_level,
)


DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "datasets"
    / "knowledge_health_v1.json"
)

REQUIRED_CATEGORIES = {
    "single_document",
    "cross_document",
    "conflict",
    "no_answer",
    "adversarial",
}
DIMENSIONS = ("data", "retrieval", "trust")


def load_dataset() -> dict:
    with DATASET_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _build_documents(raw_documents: list[dict]) -> list[Document]:
    documents = []
    for index, raw in enumerate(raw_documents):
        documents.append(
            Document(
                knowledge_base_id="gold_kb",
                display_name=f"gold-{index}.pdf",
                storage_path="gold.pdf",
                size_bytes=1,
                page_count=int(raw.get("page_count") or 0),
                sha256=raw["sha256"],
                status=raw["status"],
                version_family=raw.get("version_family"),
                published_at=_as_date(raw.get("published_at")),
                effective_at=_as_date(raw.get("effective_at")),
                superseded_at=_as_date(raw.get("superseded_at")),
            )
        )
    return documents


def _run_case(case: dict) -> dict:
    data = compute_data_health(
        _build_documents(case.get("documents", [])),
        bool(case.get("index_verified")),
    )
    retrieval = compute_retrieval_health(case.get("messages", []))
    trust = compute_trust_health(case.get("claims", []))
    actual_actions = (
        list(data["actions"]) + list(retrieval["actions"]) + list(trust["actions"])
    )
    overall = aggregate_overall_score(
        data["score"],
        retrieval["score"],
        trust["score"],
    )
    actual_level = None if overall is None else get_health_level(overall)
    return {
        "data": data,
        "retrieval": retrieval,
        "trust": trust,
        "actions": actual_actions,
        "overall": overall,
        "level": actual_level,
    }


def _scores(actual: dict) -> dict[str, float | None]:
    return {
        "data": actual["data"]["score"],
        "retrieval": actual["retrieval"]["score"],
        "trust": actual["trust"]["score"],
    }


def _deduction_codes(actual: dict) -> dict[str, set[str]]:
    return {
        "data": {item["code"] for item in actual["data"]["deductions"]},
        "retrieval": {item["code"] for item in actual["retrieval"]["deductions"]},
        "trust": {item["code"] for item in actual["trust"]["deductions"]},
    }


def _action_codes(actual: dict) -> set[str]:
    return {item["code"] for item in actual["actions"]}


def _assert_gold(case: dict, actual: dict) -> None:
    expected = case["expected"]
    scores = _scores(actual)
    for dimension in DIMENSIONS:
        expected_score = expected[f"{dimension}_score"]
        actual_score = scores[dimension]
        if expected_score is None:
            assert actual_score is None, (
                f"{case['id']}: {dimension} expected None, got {actual_score}"
            )
        else:
            assert actual_score is not None
            assert actual_score == pytest.approx(expected_score, abs=0.01), (
                f"{case['id']}: {dimension} score {actual_score} != {expected_score}"
            )

    actual_codes = _deduction_codes(actual)
    for dimension in DIMENSIONS:
        required = set(expected["deduction_codes"].get(dimension, []))
        forbidden = set(expected["forbidden_deduction_codes"].get(dimension, []))
        assert required.issubset(actual_codes[dimension]), (
            f"{case['id']}: {dimension} missing deductions {required - actual_codes[dimension]}"
        )
        assert forbidden.isdisjoint(actual_codes[dimension]), (
            f"{case['id']}: {dimension} forbidden deductions hit {forbidden & actual_codes[dimension]}"
        )

    actual_actions = _action_codes(actual)
    required_actions = set(expected.get("action_codes", []))
    forbidden_actions = set(expected.get("forbidden_action_codes", []))
    assert required_actions.issubset(actual_actions), (
        f"{case['id']}: missing actions {required_actions - actual_actions}"
    )
    assert forbidden_actions.isdisjoint(actual_actions), (
        f"{case['id']}: forbidden actions hit {forbidden_actions & actual_actions}"
    )

    assert actual["level"] == expected.get("overall_level"), (
        f"{case['id']}: level {actual['level']} != {expected.get('overall_level')}"
    )
    # formula 1.1 independent aggregation contract when all dimensions computable
    if actual["overall"] is not None:
        assert actual["overall"] == pytest.approx(
            0.4 * scores["data"] + 0.3 * scores["retrieval"] + 0.3 * scores["trust"],
            abs=0.01,
        )


# ---- dataset contract ----


def test_dataset_contract_and_coverage():
    dataset = load_dataset()
    assert dataset["schema_version"] == 1
    assert dataset["id"] == "deepsearcher-knowledge-health"
    assert dataset["version"] == "1.0.0"
    cases = dataset["cases"]
    assert len(cases) >= 16  # coverage gate, not a schema requirement
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"

    categories = [case["category"] for case in cases]
    for category in REQUIRED_CATEGORIES:
        assert categories.count(category) >= 2, f"category {category} needs >= 2 cases"
    boundary_count = sum(1 for case in cases if case.get("boundary"))
    assert boundary_count >= 4, "at least 4 boundary cases"

    for case in cases:
        expected = case["expected"]
        assert "deduction_codes" in expected
        assert "forbidden_deduction_codes" in expected
        for dimension in DIMENSIONS:
            assert dimension in expected["deduction_codes"]
            assert dimension in expected["forbidden_deduction_codes"]
        assert isinstance(expected.get("action_codes", []), list)
        assert isinstance(expected.get("forbidden_action_codes", []), list)
        assert expected["overall_level"] in {None, "healthy", "warning", "critical"}


def test_every_case_passes_gold():
    dataset = load_dataset()
    for case in dataset["cases"]:
        _assert_gold(case, _run_case(case))


def test_permutation_case_is_order_invariant():
    dataset = load_dataset()
    permutation = next(case for case in dataset["cases"] if case["id"] == "permutation-series")
    baselines = {
        seed: _deduction_codes(_run_case(permutation))
        for seed in (0, 1, 2)
    }
    assert len({tuple(sorted(codes)) for codes in baselines.values()}) == 1


def test_shuffled_inputs_are_equivalent():
    dataset = load_dataset()
    for index, case in enumerate(dataset["cases"]):
        baseline = _run_case(case)
        rng = random.Random(42 + index)
        shuffled = dict(case)
        shuffled["documents"] = list(case.get("documents", []))
        rng.shuffle(shuffled["documents"])
        shuffled["messages"] = list(case.get("messages", []))
        rng.shuffle(shuffled["messages"])
        shuffled["claims"] = list(case.get("claims", []))
        rng.shuffle(shuffled["claims"])
        variant = _run_case(shuffled)
        assert _scores(variant) == _scores(baseline)
        assert _deduction_codes(variant) == _deduction_codes(baseline)
        assert _action_codes(variant) == _action_codes(baseline)
        assert variant["level"] == baseline["level"]
