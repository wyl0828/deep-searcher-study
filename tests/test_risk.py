import pytest

from deepsearcher.risk import classify_query_risk, default_risk_profile


@pytest.mark.parametrize(
    ("query", "query_type"),
    [
        ("今年公司最高报销额度是多少？", "financial_policy"),
        ("普通成员是否可以删除审计日志？", "legal_compliance"),
        ("合同规定的赔偿责任是什么？", "financial_policy"),
        ("这种药物的推荐剂量是多少？", "health_safety"),
    ],
)
def test_decision_questions_in_sensitive_domains_are_high_risk(query, query_type):
    profile = classify_query_risk(query)

    assert profile["risk_level"] == "high"
    assert profile["query_type"] == query_type
    assert profile["requirements"]["require_decisive_entailment"] is True
    assert profile["requirements"]["allow_unknown_entailment"] is False


def test_high_risk_quantitative_decision_requires_independent_sources():
    profile = classify_query_risk("今年公司最高报销额度是多少？")

    assert "QUANTITATIVE_DECISION" in profile["risk_factors"]
    assert profile["requirements"]["minimum_evidence_count"] == 2
    assert profile["requirements"]["minimum_distinct_source_count"] == 2


@pytest.mark.parametrize(
    "query",
    [
        "ACL 检索过滤的实现原理是什么？",
        "总结这份报销制度文档的章节结构。",
        "解释审计日志在系统架构中的作用。",
        "Milvus 和 PostgreSQL 有什么区别？",
    ],
)
def test_learning_and_architecture_questions_are_not_promoted_by_keyword_only(query):
    assert classify_query_risk(query)["risk_level"] == "medium"


def test_default_profile_is_backward_compatible_medium_policy():
    profile = default_risk_profile()

    assert profile["risk_level"] == "medium"
    assert profile["requirements"]["minimum_evidence_count"] == 1
    assert profile["requirements"]["allow_unknown_entailment"] is True


def test_classifier_does_not_echo_original_query():
    secret = "sk-private-value"

    assert secret not in str(classify_query_risk(f"报销额度是多少？{secret}"))
