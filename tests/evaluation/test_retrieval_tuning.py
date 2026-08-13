from evaluation.retrieval_tuning import candidate_score, multi_document_sample_ids, parameter_grid
from evaluation.retrieval_compare import DEFAULT_DATASET


def test_parameter_grid_matches_release_experiment_contract():
    grid = parameter_grid()

    assert len(grid) == 108
    assert {item["candidate_multiplier"] for item in grid} == {1, 2, 3, 4}
    assert {item["rrf_k"] for item in grid} == {5, 20, 60}
    assert {item["dense_anchors"] for item in grid} == {0, 1, 2}
    assert all(item["diversity_tolerance"] == 0.1 for item in grid)


def test_workspace_dataset_has_eight_multi_document_samples():
    assert len(multi_document_sample_ids(DEFAULT_DATASET)) == 8


def test_candidate_score_prioritizes_document_coverage_then_quality():
    report = {
        "metrics": {
            "hybrid": {
                "full_document_coverage_rate": 0.625,
                "retrieval_recall_at_k": 0.88,
                "mrr": 0.72,
                "search_latency_ms": {"p95": 420},
            }
        }
    }

    assert candidate_score(report) == (0.625, 0.88, 0.72, -420.0)
