import pytest

from deepsearcher.agent.selection import (
    parse_one_based_index,
    validate_string_list,
    validate_zero_based_indices,
)


@pytest.mark.parametrize(
    ("response", "selected", "fallback", "reason"),
    [
        ("1", [0], False, None),
        ("2", [1], False, None),
        ("", [0], True, "invalid_index_format"),
        ("agent 2", [0], True, "invalid_index_format"),
        ("1 2", [0], True, "invalid_index_format"),
        ("-1", [0], True, "invalid_index_format"),
        ("0", [0], True, "index_out_of_range"),
        ("3", [0], True, "index_out_of_range"),
    ],
)
def test_agent_index_is_a_strict_bounded_enum(response, selected, fallback, reason):
    decision = parse_one_based_index(response, upper_bound=2)

    assert decision.values == selected
    assert decision.fallback_used is fallback
    assert decision.reason == reason


def test_string_list_filters_types_empty_duplicates_limits_and_previous_queries():
    decision = validate_string_list(
        [
            " first ",
            "",
            3,
            "first",
            "previous",
            "second",
            "third",
        ],
        max_items=2,
        excluded=["previous"],
    )

    assert decision.values == ["first", "second"]
    assert decision.rejected == [
        "<empty>",
        "<int>",
        "<duplicate>",
        "<previous_query>",
        "<over_limit>",
    ]
    assert decision.fallback_used is False
    assert decision.reason == "invalid_items_filtered"


@pytest.mark.parametrize("value", [None, {}, "['query']", (["query"],)])
def test_invalid_string_list_container_uses_declared_fallback(value):
    decision = validate_string_list(
        value,
        max_items=4,
        fallback=["original question"],
        fallback_on_empty=True,
    )

    assert decision.values == ["original question"]
    assert decision.fallback_used is True
    assert decision.reason == "invalid_list_type"


def test_empty_initial_query_list_uses_original_question():
    decision = validate_string_list(
        [],
        max_items=4,
        fallback=["original question"],
        fallback_on_empty=True,
    )

    assert decision.values == ["original question"]
    assert decision.fallback_used is True
    assert decision.reason == "empty_or_invalid_selection"


def test_document_indices_reject_negative_float_bool_out_of_range_and_duplicates():
    decision = validate_zero_based_indices(
        [-1, 0, 1.0, True, 2, 3, 0],
        upper_bound=3,
    )

    assert decision.values == [0, 2]
    assert decision.rejected == [
        "-1",
        "<float>",
        "<bool>",
        "3",
        "<duplicate>",
    ]
    assert decision.fallback_used is False
    assert decision.reason == "invalid_items_filtered"


def test_all_invalid_document_indices_safely_select_no_documents():
    decision = validate_zero_based_indices([-1, 4, 1.5], upper_bound=2)

    assert decision.values == []
    assert decision.fallback_used is True
    assert decision.reason == "no_valid_indices"
