import main


def test_query_api_keeps_legacy_response_without_trace(monkeypatch):
    monkeypatch.setattr(main, "query", lambda _question, _max_iter: ("答案", [], 12))

    response = main.perform_query("问题", 3, False)

    assert response == {"result": "答案", "consume_token": 12}


def test_query_api_returns_trace_when_requested(monkeypatch):
    expected_trace = {"version": 1, "agent": {"name": "ChainOfRAG"}}
    monkeypatch.setattr(
        main,
        "query_with_trace",
        lambda _question, _max_iter: ("答案", [], 12, expected_trace),
    )

    response = main.perform_query("问题", 3, True)

    assert response == {
        "result": "答案",
        "consume_token": 12,
        "trace": expected_trace,
    }
