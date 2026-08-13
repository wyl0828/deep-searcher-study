from deepsearcher.freshness import classify_query_freshness, sanitize_freshness_intent


def test_freshness_classifier_separates_current_effective_and_latest_publication():
    assert classify_query_freshness("当前有效的报销政策是什么？")["mode"] == "current"


def test_repository_version_language_is_not_a_temporal_freshness_request():
    for query in (
        "当前项目应如何解释 L2 和 score 字段？",
        "当前配置是否启用了 Hybrid？",
        "当前实现如何生成 Citation？",
        "DeepSearcher 当前本地选择哪种部署？",
        "当前生产配置是否启用了 Hybrid？",
    ):
        assert classify_query_freshness(query)["mode"] == "none"
        assert classify_query_freshness(query)["required"] is False
    assert classify_query_freshness("最新报销政策是什么？")["mode"] == "latest_effective"
    assert classify_query_freshness("最新发布的产品公告是什么？")["mode"] == ("latest_published")
    assert classify_query_freshness("近期有哪些政策变化？")["mode"] == "recent"
    assert classify_query_freshness("解释向量检索原理")["required"] is False


def test_freshness_profile_is_fixed_shape_and_rejects_client_downgrade():
    profile = classify_query_freshness("最新政策是什么？")

    assert sanitize_freshness_intent(profile) == profile
    assert sanitize_freshness_intent({**profile, "required": False}) is None
    assert sanitize_freshness_intent({**profile, "mode": "none"}) is None
    assert sanitize_freshness_intent({**profile, "query": "secret"}) == profile
