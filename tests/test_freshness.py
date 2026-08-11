from deepsearcher.freshness import classify_query_freshness, sanitize_freshness_intent


def test_freshness_classifier_separates_current_effective_and_latest_publication():
    assert classify_query_freshness("当前有效的报销政策是什么？")["mode"] == "current"
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
