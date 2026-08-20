"""Versioned final-answer organization strategies.

The strategy in this module is deliberately downstream of retrieval and support
selection.  It may organize already admitted evidence, but it must not change
retrieval scores, evidence admission, or Trust policy.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from deepsearcher.vector_db.base import RetrievalResult

DEFAULT_STRATEGY_VERSION = "default.v1"
DEFINITION_FIRST_VERSION = "definition-first.v1"

_DEFINITION_QUERY_MARKERS = (
    "什么是",
    "是什么",
    "指什么",
    "是何",
    "what is",
    "what's",
    "define",
    "definition of",
)
_DEFINITION_RELATIONS = (
    "是一个",
    "是一种",
    "是指",
    "属于",
    "是",
    "is a",
    "is an",
    "is",
    "refers to",
)
_DEFINITION_CONTENT_HINTS = (
    "基于rag",
    "文档问答项目",
    "document qa",
    "rag project",
)
_SENTENCE_BOUNDARY = re.compile(r"[^\n。！？!?；;]+(?:[。！？!?；;]+|$)")
_EVIDENCE_MARKER = re.compile(r"\[\s*(E[1-9]\d{0,2})\s*\]", re.IGNORECASE)
_MARKDOWN_PREFIX = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", text)


def _normalize_query_for_classification(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _extract_subject(query: str) -> str:
    """Extract the compact subject for the small, versioned definition grammar."""

    compact = _normalize(query)
    if not compact:
        return ""
    prefixes = ("什么是", "what is", "what's", "define", "definition of")
    suffixes = ("是什么", "指什么", "是何")
    for prefix in prefixes:
        prefix_compact = _normalize(prefix)
        if compact.startswith(prefix_compact):
            compact = compact[len(prefix_compact) :]
            break
    for suffix in suffixes:
        suffix_compact = _normalize(suffix)
        if compact.endswith(suffix_compact):
            compact = compact[: -len(suffix_compact)]
            break
    return re.sub(r"[^\w\u3400-\u9fff.-]+", "", compact, flags=re.UNICODE)


def _contains_definition_relation(sentence: str, subject: str) -> bool:
    normalized_sentence = _normalize(sentence)
    if not subject or subject not in normalized_sentence:
        return False
    subject_position = normalized_sentence.find(subject)
    after_subject = normalized_sentence[subject_position + len(subject) :]
    relation_window = after_subject[:160]
    return any(
        relation_window.startswith(_normalize(relation)) or _normalize(relation) in relation_window
        for relation in _DEFINITION_RELATIONS
    ) or any(_normalize(hint) in relation_window for hint in _DEFINITION_CONTENT_HINTS)


def _evidence_text(result: RetrievalResult) -> str:
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    wider_text = metadata.get("wider_text")
    if isinstance(wider_text, str) and wider_text.strip():
        return wider_text
    return str(result.text or "")


def _definition_sentence(text: str, subject: str) -> str | None:
    for match in _SENTENCE_BOUNDARY.finditer(str(text or "")):
        sentence = _MARKDOWN_PREFIX.sub("", match.group(0).strip()).strip()
        if sentence and _contains_definition_relation(sentence, subject):
            return sentence
    normalized_text = _normalize(text)
    if (
        subject
        and subject in normalized_text
        and any(_normalize(hint) in normalized_text for hint in _DEFINITION_CONTENT_HINTS)
    ):
        raw_match = re.search(re.escape(subject), str(text or ""), flags=re.IGNORECASE)
        if raw_match:
            tail = str(text)[raw_match.start() :]
            comma = re.search(r"[，,]", tail)
            punctuation = re.search(r"[。！？!?]", tail)
            end_candidates = [match.start() for match in (comma, punctuation) if match]
            end = min(end_candidates) if end_candidates else len(tail)
            candidate = tail[:end].strip(" \t\r\n，,")
            if candidate:
                return candidate + "。"
    return None


def parse_rendered_evidence(rendered_evidence: str) -> dict[str, str]:
    """Parse the exact Evidence bodies sent to the final-answer model."""

    return {
        match.group(1).upper(): html.unescape(match.group(2))
        for match in re.finditer(
            r'<Evidence\s+id="(E[1-9]\d{0,2})"[^>]*>\s*(.*?)\s*</Evidence>',
            str(rendered_evidence or ""),
            flags=re.IGNORECASE | re.DOTALL,
        )
    }


def _first_material_sentence(answer: str) -> str:
    for raw_line in str(answer or "").splitlines():
        line = _MARKDOWN_PREFIX.sub("", raw_line.strip()).strip()
        if not line or _EVIDENCE_MARKER.fullmatch(line):
            continue
        match = _SENTENCE_BOUNDARY.search(line)
        if match is None:
            return line
        return (match.group(0) + line[match.end() :]).strip()
    return ""


@dataclass(frozen=True)
class AnswerStrategyPlan:
    """Request-scoped, serializable answer organization plan."""

    version: str
    query_class: str
    decision: str
    subject_anchor: str | None
    primary_result_ids: tuple[int, ...]
    primary_evidence_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    fallback_used: bool
    fallback_reason: str | None
    output_gate: str = "not_run"

    def with_output_gate(self, *, used: bool, reason: str | None) -> "AnswerStrategyPlan":
        return replace(
            self,
            fallback_used=self.fallback_used or used,
            fallback_reason=reason if used else self.fallback_reason,
            output_gate="definition_sentence_fallback" if used else "passed",
        )

    def as_trace(self) -> dict:
        return {
            "version": self.version,
            "query_class": self.query_class,
            "decision": self.decision,
            "subject_anchor": self.subject_anchor,
            "primary_evidence_ids": list(self.primary_evidence_ids),
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "intermediate_context_role": (
                "secondary_context" if self.query_class == "concept_definition" else "default"
            ),
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "output_gate": self.output_gate,
        }

    def prompt_block(self) -> str:
        primary = ",".join(self.primary_evidence_ids) or "none"
        supporting = ",".join(self.supporting_evidence_ids) or "none"
        if self.query_class == "concept_definition" and self.primary_evidence_ids:
            instruction = (
                "The first material sentence must directly answer what the subject is and "
                "cite one of the primary definition Evidence IDs. Use supporting evidence "
                "and intermediate evidence only after that sentence."
            )
        elif self.query_class == "concept_definition":
            instruction = (
                "No direct definition Evidence was found. Do not invent a definition from "
                "titles or process descriptions; answer only what the supplied evidence supports."
            )
        else:
            instruction = "Use the existing evidence-grounded answer organization."
        return (
            f'<AnswerStrategy version="{html.escape(self.version, quote=True)}" '
            f'query_class="{html.escape(self.query_class, quote=True)}">\n'
            f"Primary definition Evidence IDs: {primary}\n"
            f"Supporting Evidence IDs: {supporting}\n"
            f"Intermediate evidence role: "
            f"{'secondary context' if self.query_class == 'concept_definition' else 'default'}\n"
            f"Answer-order instruction: {instruction}\n"
            "</AnswerStrategy>"
        )


def plan_answer_strategy(
    query: str,
    results: Sequence[RetrievalResult],
    *,
    evidence_ids: Mapping[int, str] | None = None,
    evidence_texts: Mapping[str, str] | None = None,
    rendered_evidence: str | None = None,
) -> AnswerStrategyPlan:
    """Build a versioned plan from already selected final evidence."""

    normalized_query = _normalize_query_for_classification(query)
    is_definition = any(marker in normalized_query for marker in _DEFINITION_QUERY_MARKERS)
    visible_ids = evidence_ids or {}
    snapshot_texts = evidence_texts or {}

    def candidate_text(result: RetrievalResult) -> str:
        evidence_id = visible_ids.get(id(result))
        return (
            snapshot_texts.get(evidence_id, _evidence_text(result))
            if evidence_id
            else _evidence_text(result)
        )

    if not is_definition:
        return AnswerStrategyPlan(
            version=DEFAULT_STRATEGY_VERSION,
            query_class="other",
            decision="default",
            subject_anchor=None,
            primary_result_ids=(),
            primary_evidence_ids=(),
            supporting_evidence_ids=tuple(
                visible_ids[id(result)] for result in results if id(result) in visible_ids
            ),
            fallback_used=False,
            fallback_reason=None,
        )

    subject = _extract_subject(query)
    primary_results: list[RetrievalResult] = []
    for result in results:
        if id(result) not in visible_ids:
            continue
        if _definition_sentence(candidate_text(result), subject):
            primary_results.append(result)
        if len(primary_results) >= 2:
            break
    if not primary_results and snapshot_texts:
        result_by_evidence_id = {
            visible_ids[id(result)]: result for result in results if id(result) in visible_ids
        }
        for evidence_id, text in snapshot_texts.items():
            result = result_by_evidence_id.get(evidence_id)
            if result is not None and _definition_sentence(text, subject):
                primary_results.append(result)
            if len(primary_results) >= 2:
                break
    if not primary_results and rendered_evidence:
        rendered_snapshot = parse_rendered_evidence(rendered_evidence)
        result_by_evidence_id = {
            visible_ids[id(result)]: result for result in results if id(result) in visible_ids
        }
        for evidence_id, text in rendered_snapshot.items():
            result = result_by_evidence_id.get(evidence_id)
            if result is not None and _definition_sentence(text, subject):
                primary_results.append(result)
            if len(primary_results) >= 2:
                break
    primary_object_ids = {id(result) for result in primary_results}
    primary_evidence_ids = tuple(visible_ids[id(result)] for result in primary_results)
    supporting_evidence_ids = tuple(
        visible_ids[id(result)]
        for result in results
        if id(result) in visible_ids and id(result) not in primary_object_ids
    )
    if primary_results:
        return AnswerStrategyPlan(
            version=DEFINITION_FIRST_VERSION,
            query_class="concept_definition",
            decision="definition_first",
            subject_anchor=subject or None,
            primary_result_ids=tuple(id(result) for result in primary_results),
            primary_evidence_ids=primary_evidence_ids,
            supporting_evidence_ids=supporting_evidence_ids,
            fallback_used=False,
            fallback_reason=None,
        )
    definition_outside_snapshot = any(
        _definition_sentence(candidate_text(result), subject)
        for result in results
        if id(result) not in visible_ids
    )
    return AnswerStrategyPlan(
        version=DEFINITION_FIRST_VERSION,
        query_class="concept_definition",
        decision="fallback_default",
        subject_anchor=subject or None,
        primary_result_ids=(),
        primary_evidence_ids=(),
        supporting_evidence_ids=supporting_evidence_ids,
        fallback_used=True,
        fallback_reason=(
            "definition_evidence_outside_snapshot"
            if definition_outside_snapshot
            else "definition_evidence_not_found"
        ),
    )


def enforce_answer_order(
    answer: str,
    plan: AnswerStrategyPlan,
    *,
    results: Iterable[RetrievalResult],
    evidence_texts: Mapping[str, str],
) -> tuple[str, AnswerStrategyPlan]:
    """Enforce the first-sentence contract without inventing or paraphrasing facts."""

    if plan.decision != "definition_first" or not plan.primary_evidence_ids:
        if plan.output_gate == "not_run":
            return answer, replace(plan, output_gate="not_applicable")
        return answer, plan

    first_sentence = _first_material_sentence(answer)
    answer_prefix = _MARKDOWN_PREFIX.sub("", str(answer or "").lstrip()).strip()
    first_markers = {marker.upper() for marker in _EVIDENCE_MARKER.findall(first_sentence)}
    primary_texts = [
        evidence_texts[evidence_id]
        for evidence_id in plan.primary_evidence_ids
        if evidence_id in evidence_texts
    ]
    first_is_definition = bool(
        plan.subject_anchor
        and _normalize(answer_prefix).startswith(plan.subject_anchor)
        and plan.subject_anchor in _normalize(first_sentence)
        and _contains_definition_relation(first_sentence, plan.subject_anchor)
        and first_markers.intersection(plan.primary_evidence_ids)
        and any(plan.subject_anchor in _normalize(text) for text in primary_texts)
    )
    result_by_id = {id(result): result for result in results}
    for result_id, evidence_id in zip(plan.primary_result_ids, plan.primary_evidence_ids):
        result = result_by_id.get(result_id)
        source_text = evidence_texts.get(evidence_id) or (_evidence_text(result) if result else "")
        sentence = _definition_sentence(source_text, plan.subject_anchor or "")
        if sentence:
            return f"{sentence} [{evidence_id}]", replace(
                plan,
                fallback_used=not first_is_definition,
                fallback_reason=(None if first_is_definition else "definition_sentence_fallback"),
                output_gate="canonical_definition",
            )
    return answer, replace(plan, output_gate="definition_sentence_unavailable")
