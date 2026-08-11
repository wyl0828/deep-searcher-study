from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections.abc import AsyncIterator
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from deepsearcher.freshness import sanitize_freshness_intent
from deepsearcher.grounding import MAX_GROUNDING_EVIDENCE_TEXT
from deepsearcher.provenance import sanitize_trust_provenance
from deepsearcher.temporal import extract_document_temporal_metadata
from deepsearcher.trace import redact_sensitive_text
from deepsearcher.trust import build_temporal_context
from deepsearcher.versioning import extract_document_version_metadata
from deepsearcher.web_search.tavily import canonical_public_url
from frontend.product.backend import backend_request_headers
from frontend.product.errors import ProductError
from frontend.product.models import AnswerClaim, Citation, Conversation, Document, Message
from frontend.product.schemas import MessageResponse

BACKEND_URL = os.environ.get("DEEPSEARCHER_API_URL", "http://127.0.0.1:8500").rstrip("/")

QUERY_ERROR_MESSAGES = {
    "RUNTIME_INITIALIZATION_FAILED": (
        "问答服务正在恢复依赖，请稍后重试。",
        503,
        True,
    ),
    "VECTOR_DB_UNAVAILABLE": (
        "向量检索服务暂时不可用，请稍后重试。",
        503,
        True,
    ),
    "VECTOR_COLLECTION_NOT_FOUND": (
        "当前知识库的检索索引不存在，请重新处理资料。",
        409,
        False,
    ),
    "VECTOR_DIMENSION_MISMATCH": (
        "当前知识库索引与 Embedding 模型不兼容，需要重新处理资料。",
        409,
        False,
    ),
    "VECTOR_COLLECTION_MANIFEST_MISSING": (
        "当前知识库索引缺少模型版本信息，需要重新处理全部资料。",
        409,
        False,
    ),
    "VECTOR_COLLECTION_MANIFEST_INVALID": (
        "当前知识库索引版本信息损坏，需要重新处理全部资料。",
        409,
        False,
    ),
    "VECTOR_COLLECTION_MANIFEST_UNSUPPORTED": (
        "当前向量存储无法校验模型版本，请更换配置或重建索引。",
        409,
        False,
    ),
    "VECTOR_EMBEDDING_PROFILE_MISMATCH": (
        "Embedding 模型已变化，当前知识库需要使用全部资料重建索引。",
        409,
        False,
    ),
    "VECTOR_SEARCH_FAILED": (
        "向量检索没有完成，请稍后重试。",
        502,
        True,
    ),
    "VECTOR_LIST_FAILED": (
        "暂时无法读取知识库索引，请稍后重试。",
        502,
        True,
    ),
    "QUERY_TIMEOUT": (
        "本次检索超过时间限制，请稍后重试。",
        504,
        True,
    ),
    "QUERY_FAILED": (
        "问答服务没有完成本次查询，请稍后重试。",
        502,
        True,
    ),
}
SAFE_STAGE_EVENTS = {
    "started",
    "contextualization",
    "routing",
    "iteration",
    "retrieval",
    "web_search",
    "support",
    "reflection",
}
TERMINAL_EVENTS = {"completed", "error", "cancelled"}
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_SSE_LINE_CHARS = 1_000_000
MAX_SSE_EVENT_CHARS = 1_000_000
MAX_SSE_DATA_LINES = 256
MAX_PERSISTED_CITATIONS = 20


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_optional_int(value: object, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= minimum else None


def _safe_text(value: object, *, max_length: int) -> str | None:
    return redact_sensitive_text(value, max_length=max_length)


def _safe_identifier(value: object, *, max_length: int = 128) -> str | None:
    identifier = str(value or "")
    if len(identifier) > max_length or re.fullmatch(r"[A-Za-z0-9._:-]+", identifier) is None:
        return None
    return identifier


def _safe_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = [safe for item in value[:8] if (safe := _safe_text(item, max_length=160)) is not None]
    return items or None


def _safe_reason_codes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            code
            for item in value[:16]
            if (code := _safe_identifier(item, max_length=64)) is not None
        )
    )


def _safe_confidence(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return confidence if math.isfinite(confidence) and 0 <= confidence <= 1 else None


def _safe_citation_spans(
    value: object,
    *,
    evidence_to_citation: dict[str, int] | None = None,
    evidence_text_by_id: dict[str, str] | None = None,
) -> list[dict]:
    if not isinstance(value, list):
        return []
    spans: list[dict] = []
    for item in value[:MAX_PERSISTED_CITATIONS]:
        if not isinstance(item, dict):
            continue
        evidence_id = _safe_identifier(item.get("evidence_id"), max_length=16)
        match_type = _safe_identifier(item.get("match_type"), max_length=24)
        if evidence_id is None or match_type not in {
            "normalized_exact",
            "sentence_overlap",
            "not_found",
        }:
            continue
        start = _safe_optional_int(item.get("start"))
        end = _safe_optional_int(item.get("end"))
        quote = _safe_text(item.get("text"), max_length=1200) or ""
        score = _safe_confidence(item.get("score"))
        if match_type == "not_found":
            start = None
            end = None
            quote = ""
            score = 0.0
        elif (
            start is None
            or end is None
            or end <= start
            or end > MAX_GROUNDING_EVIDENCE_TEXT
            or not quote
            or score is None
        ):
            continue
        if evidence_text_by_id is not None and match_type != "not_found":
            evidence_text = evidence_text_by_id.get(evidence_id)
            if (
                evidence_text is None
                or end is None
                or end > len(evidence_text)
                or evidence_text[start:end] != quote
            ):
                continue
        span = {
            "evidence_id": evidence_id,
            "start": start,
            "end": end,
            "text": quote,
            "match_type": match_type,
            "score": score,
        }
        if evidence_to_citation is not None:
            citation_index = evidence_to_citation.get(evidence_id)
            if citation_index is None:
                continue
            span["citation_index"] = citation_index
            span.pop("evidence_id")
        spans.append(span)
    return spans


def _safe_consistency_checks(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    checks: list[dict] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        kind = _safe_identifier(item.get("kind"), max_length=24)
        status = _safe_identifier(item.get("status"), max_length=24)
        reason_code = _safe_identifier(item.get("reason_code"), max_length=64)
        if kind is None or status is None or reason_code is None:
            continue
        check = {"kind": kind, "status": status, "reason_code": reason_code}
        for field in ("claim_values", "missing_values"):
            raw_values = item.get(field)
            if isinstance(raw_values, list):
                check[field] = [
                    text
                    for raw in raw_values[:16]
                    if (text := _safe_text(raw, max_length=64)) is not None
                ]
        checks.append(check)
    return checks


def _safe_risk_checks(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    checks: list[dict] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        kind = _safe_identifier(item.get("kind"), max_length=32)
        status = _safe_identifier(item.get("status"), max_length=16)
        reason_code = _safe_identifier(item.get("reason_code"), max_length=64)
        if kind is None or status not in {"passed", "failed"} or reason_code is None:
            continue
        check: dict = {"kind": kind, "status": status, "reason_code": reason_code}
        for field in ("actual", "required"):
            raw = item.get(field)
            if isinstance(raw, bool):
                continue
            if isinstance(raw, int):
                check[field] = max(raw, 0)
            elif (safe := _safe_identifier(raw, max_length=64)) is not None:
                check[field] = safe
        checks.append(check)
    return checks


def _safe_trust_claims(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    claims: list[dict] = []
    for item in value[:64]:
        if not isinstance(item, dict):
            continue
        text = _safe_text(item.get("text"), max_length=600)
        if text is None:
            continue
        claim = {
            "index": _safe_nonnegative_int(item.get("index")),
            "text": text,
            "support_status": _safe_identifier(item.get("support_status"), max_length=32),
            "structural_support_status": _safe_identifier(
                item.get("structural_support_status"), max_length=32
            ),
            "citation_status": _safe_identifier(item.get("citation_status"), max_length=24),
            "entailment_status": _safe_identifier(item.get("entailment_status"), max_length=24),
            "entailment_method": _safe_identifier(item.get("entailment_method"), max_length=32),
            "entailment_checker": _safe_identifier(item.get("entailment_checker"), max_length=64),
            "entailment_checker_version": _safe_identifier(
                item.get("entailment_checker_version"), max_length=32
            ),
            "consistency_status": _safe_identifier(item.get("consistency_status"), max_length=24),
            "consistency_checks": _safe_consistency_checks(item.get("consistency_checks")),
            "citation_spans": _safe_citation_spans(item.get("citation_spans")),
            "confidence": _safe_confidence(item.get("confidence")),
            "risk_status": _safe_identifier(item.get("risk_status"), max_length=24),
            "risk_checks": _safe_risk_checks(item.get("risk_checks")),
            "reason_codes": _safe_reason_codes(item.get("reason_codes")),
        }
        claims.append({key: item for key, item in claim.items() if item is not None})
    return claims


def _safe_trust_section(value: object) -> dict:
    section = value if isinstance(value, dict) else {}
    return {
        "trust_status": _safe_identifier(section.get("trust_status"), max_length=32),
        "claim_count": _safe_nonnegative_int(section.get("claim_count")),
        "supported_claim_count": _safe_nonnegative_int(section.get("supported_claim_count")),
        "claims": _safe_trust_claims(section.get("claims")),
    }


def _safe_trust_details(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    verification_level = _safe_identifier(value.get("verification_level"), max_length=64)
    if verification_level is None:
        return None
    temporal_context = _safe_temporal_context(value.get("temporal_context"))
    result = {
        "verification_level": verification_level,
        "evidence_snapshot_available": bool(value.get("evidence_snapshot_available")),
        "entailment": _safe_entailment_details(value.get("entailment")),
        "risk": _safe_risk_details(value.get("risk")),
        "temporal_context": temporal_context,
        "input": _safe_trust_section(value.get("input")),
        "output": _safe_trust_section(value.get("output")),
        "limitations": _safe_reason_codes(value.get("limitations")),
    }
    freshness = sanitize_freshness_intent(value.get("freshness"))
    if freshness is not None:
        result["freshness"] = freshness
    provenance = sanitize_trust_provenance(value.get("provenance"))
    if provenance is not None:
        provenance_temporal = provenance.get("temporal")
        if isinstance(provenance_temporal, dict) and (
            not temporal_context
            or provenance_temporal.get("reference_date") != temporal_context.get("reference_date")
            or provenance_temporal.get("timezone") != temporal_context.get("timezone")
        ):
            return None
        result["provenance"] = provenance
    return result


def _safe_entailment_details(value: object) -> dict:
    details = value if isinstance(value, dict) else {}
    result = {
        "version": _safe_nonnegative_int(details.get("version")),
        "checker": _safe_identifier(details.get("checker"), max_length=64),
        "checker_version": _safe_identifier(details.get("checker_version"), max_length=32),
        "status": _safe_identifier(details.get("status"), max_length=24),
        "token_usage": _safe_nonnegative_int(details.get("token_usage")),
        "eligible_claim_count": _safe_nonnegative_int(details.get("eligible_claim_count")),
        "exact_match_count": _safe_nonnegative_int(details.get("exact_match_count")),
        "checker_claim_count": _safe_nonnegative_int(details.get("checker_claim_count")),
        "entailed_count": _safe_nonnegative_int(details.get("entailed_count")),
        "contradicted_count": _safe_nonnegative_int(details.get("contradicted_count")),
        "unknown_count": _safe_nonnegative_int(details.get("unknown_count")),
        "not_checked_count": _safe_nonnegative_int(details.get("not_checked_count")),
        "error_code": _safe_identifier(details.get("error_code"), max_length=64),
    }
    return {key: item for key, item in result.items() if item is not None}


def _safe_risk_details(value: object) -> dict:
    details = value if isinstance(value, dict) else {}
    requirements = details.get("requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    return {
        "version": _safe_nonnegative_int(details.get("version")),
        "classifier": _safe_identifier(details.get("classifier"), max_length=64),
        "classifier_version": _safe_identifier(details.get("classifier_version"), max_length=32),
        "risk_level": _safe_identifier(details.get("risk_level"), max_length=16),
        "query_type": _safe_identifier(details.get("query_type"), max_length=32),
        "risk_factors": _safe_reason_codes(details.get("risk_factors")),
        "requirements": {
            "require_citation": bool(requirements.get("require_citation", True)),
            "require_decisive_entailment": bool(
                requirements.get("require_decisive_entailment", False)
            ),
            "minimum_evidence_count": _safe_nonnegative_int(
                requirements.get("minimum_evidence_count")
            ),
            "minimum_distinct_source_count": _safe_nonnegative_int(
                requirements.get("minimum_distinct_source_count")
            ),
            "allow_unknown_entailment": bool(requirements.get("allow_unknown_entailment", True)),
        },
    }


def _safe_temporal_context(value: object) -> dict:
    details = value if isinstance(value, dict) else {}
    if details.get("version") != 1 or details.get("source") != "request_clock":
        return {}
    try:
        normalized = build_temporal_context(
            reference_time=str(details.get("reference_time") or ""),
            timezone_name=str(details.get("timezone") or ""),
        )
    except ValueError:
        return {}
    if normalized["reference_date"] != details.get("reference_date"):
        return {}
    return normalized


def _safe_bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [round(float(item), 6) for item in value]
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        any(not math.isfinite(item) or item < 0 or item > 1 for item in bbox)
        or bbox[0] > bbox[2]
        or bbox[1] > bbox[3]
    ):
        return None
    return bbox


def _safe_request_id(value: object) -> str:
    request_id = str(value or "")
    return request_id if SAFE_REQUEST_ID.fullmatch(request_id) else ""


def _invalid_stream_error() -> ProductError:
    return ProductError(
        "QUERY_STREAM_INVALID",
        "问答服务返回了无效的流式响应，请稍后重试。",
        status_code=502,
        retryable=True,
    )


def _validate_stream_envelope(
    event_name: str,
    envelope: dict,
    *,
    expected_request_id: str,
    last_sequence: int,
) -> tuple[str, int]:
    if (
        event_name not in SAFE_STAGE_EVENTS | TERMINAL_EVENTS
        or envelope.get("version") != 1
        or envelope.get("event") != event_name
    ):
        raise _invalid_stream_error()
    request_id = _safe_request_id(envelope.get("request_id"))
    sequence = envelope.get("sequence")
    if (
        not request_id
        or (expected_request_id and request_id != expected_request_id)
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence != last_sequence + 1
    ):
        raise _invalid_stream_error()
    return request_id, sequence


def query_error_from_response(response: httpx.Response) -> ProductError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code in QUERY_ERROR_MESSAGES:
        message, status_code, retryable = QUERY_ERROR_MESSAGES[code]
        return ProductError(
            code,
            message,
            status_code=status_code,
            retryable=retryable,
        )
    return ProductError(
        "QUERY_FAILED",
        "问答服务没有完成本次查询，请稍后重试。",
        status_code=502,
        retryable=True,
    )


def query_error_from_event(error: dict) -> ProductError:
    code = str(error.get("code") or "QUERY_FAILED")
    if code in QUERY_ERROR_MESSAGES:
        message, status_code, retryable = QUERY_ERROR_MESSAGES[code]
        return ProductError(
            code,
            message,
            status_code=status_code,
            retryable=retryable,
        )
    return ProductError(
        "QUERY_FAILED",
        "问答服务没有完成本次查询，请稍后重试。",
        status_code=502,
        retryable=True,
    )


def _serialize_message(message: Message) -> dict:
    return MessageResponse.model_validate(message).model_dump(mode="json")


def _create_pending_messages(
    session: Session,
    *,
    conversation: Conversation,
    question: str,
) -> tuple[Message, Message]:
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=question,
        status="succeeded",
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        status="pending",
    )
    session.add_all([user_message, assistant_message])
    if conversation.title == "新对话":
        conversation.title = question[:36]
    session.commit()
    session.refresh(user_message)
    session.refresh(assistant_message)
    return user_message, assistant_message


def _finish_assistant_message(
    session: Session,
    *,
    assistant_message: Message,
    payload: dict,
) -> Message:
    assistant_message.content = str(payload.get("result") or "")
    trace = payload.get("trace") or {}
    grounding = trace.get("grounding") if isinstance(trace, dict) else None
    trust = trace.get("trust") if isinstance(trace, dict) else None
    has_structured_trust = isinstance(trust, dict) and trust.get("version") == 1
    has_structured_grounding = isinstance(grounding, dict) and grounding.get("version") == 1
    citations, evidence_to_citation = _collect_supported_citations(
        session,
        message=assistant_message,
        trace=trace,
    )
    claims = collect_answer_claims(
        session,
        message=assistant_message,
        trace=trace,
        evidence_to_citation=evidence_to_citation,
    )
    if has_structured_trust:
        policy = trust.get("policy") if isinstance(trust.get("policy"), dict) else {}
        assistant_message.trust_contract_version = 1
        assistant_message.trust_status = _safe_identifier(trust.get("trust_status"), max_length=32)
        assistant_message.safety_status = _safe_identifier(
            trust.get("safety_status"), max_length=24
        )
        assistant_message.policy_action = _safe_identifier(policy.get("action"), max_length=24)
        assistant_message.policy_profile = _safe_identifier(policy.get("profile"), max_length=32)
        assistant_message.policy_reason_codes = _safe_reason_codes(policy.get("reason_codes"))
        risk = trust.get("risk") if isinstance(trust.get("risk"), dict) else {}
        assistant_message.risk_level = _safe_identifier(risk.get("risk_level"), max_length=16)
        assistant_message.query_type = _safe_identifier(risk.get("query_type"), max_length=32)
        assistant_message.risk_factors = _safe_reason_codes(risk.get("risk_factors"))
        assistant_message.trust_details = _safe_trust_details(trust)
        provenance = (
            assistant_message.trust_details.get("provenance")
            if isinstance(assistant_message.trust_details, dict)
            else None
        )
        if isinstance(provenance, dict):
            assistant_message.provenance_contract_version = int(provenance["version"])
            assistant_message.provenance_digest = str(provenance["digest"])
        output = trust.get("output") if isinstance(trust.get("output"), dict) else {}
        output_state = _safe_identifier(output.get("trust_status"), max_length=32)
        if output_state in {
            "fully_grounded",
            "partially_grounded",
            "conflicting_evidence",
            "insufficient_evidence",
        }:
            assistant_message.answer_state = output_state
    if assistant_message.answer_state is None:
        if claims:
            statuses = {claim.support_status for claim in claims}
            supported_count = sum(
                claim.support_status in {"supported", "conflicting"} for claim in claims
            )
            if "conflicting" in statuses:
                assistant_message.answer_state = "conflicting_evidence"
            elif supported_count == len(claims):
                assistant_message.answer_state = "fully_grounded"
            elif supported_count:
                assistant_message.answer_state = "partially_grounded"
            else:
                assistant_message.answer_state = "insufficient_evidence"
        elif has_structured_grounding:
            assistant_message.answer_state = "insufficient_evidence"
        else:
            assistant_message.answer_state = "grounded" if citations else "insufficient_evidence"
    assistant_message.status = "succeeded"
    session.commit()
    session.refresh(assistant_message)
    return assistant_message


def _fail_assistant_message(
    session: Session,
    *,
    assistant_message: Message,
    message: str,
) -> None:
    assistant_message.status = "failed"
    assistant_message.answer_state = "failed"
    assistant_message.content = message
    session.commit()


async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, dict]]:
    event_name = "message"
    data_lines: list[str] = []
    event_chars = 0
    async for line in response.aiter_lines():
        if len(line) > MAX_SSE_LINE_CHARS:
            raise _invalid_stream_error()
        if not line:
            if data_lines:
                try:
                    payload = json.loads("\n".join(data_lines))
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                if isinstance(payload, dict):
                    yield event_name, payload
            event_name = "message"
            data_lines = []
            event_chars = 0
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_line = line[5:].lstrip()
            data_lines.append(data_line)
            event_chars += len(data_line)
            if len(data_lines) > MAX_SSE_DATA_LINES or event_chars > MAX_SSE_EVENT_CHARS:
                raise _invalid_stream_error()
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            yield event_name, payload


def _safe_stage_envelope(event_name: str, envelope: dict) -> dict | None:
    if event_name not in SAFE_STAGE_EVENTS or envelope.get("event") != event_name:
        return None
    raw_data = envelope.get("data")
    if not isinstance(raw_data, dict):
        return None
    data: dict = {}
    if event_name == "started":
        data["stage"] = "query_started"
    elif event_name == "contextualization":
        data["depends_on_history"] = bool(raw_data.get("depends_on_history", False))
        data["history_turn_count"] = min(
            _safe_nonnegative_int(raw_data.get("history_turn_count")),
            8,
        )
        data["fallback_used"] = bool(raw_data.get("fallback_used", False))
        reason = str(raw_data.get("reason") or "unknown")
        data["reason"] = (
            reason
            if reason
            in {
                "rewritten",
                "standalone",
                "invalid_output",
                "contextualizer_failed",
            }
            else "unknown"
        )
    elif event_name == "routing":
        agent = str(raw_data.get("agent") or "RAGAgent")
        data["agent"] = agent if re.fullmatch(r"[A-Za-z0-9_]{1,64}", agent) else "RAGAgent"
        data["fallback_used"] = bool(raw_data.get("fallback_used", False))
    elif event_name == "iteration":
        data["iteration"] = _safe_nonnegative_int(raw_data.get("iteration"))
    elif event_name == "retrieval":
        data["iteration"] = _safe_nonnegative_int(raw_data.get("iteration"))
        data["retrieved_count"] = _safe_nonnegative_int(raw_data.get("retrieved_count"))
    elif event_name == "web_search":
        status = str(raw_data.get("status") or "degraded")
        data["iteration"] = _safe_nonnegative_int(raw_data.get("iteration"))
        data["status"] = (
            status
            if status in {"disabled", "completed", "partial", "degraded", "empty"}
            else "degraded"
        )
        provider = str(raw_data.get("provider") or "unknown")
        data["provider"] = (
            provider if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", provider) else "unknown"
        )
        data["query_count"] = _safe_nonnegative_int(raw_data.get("query_count"))
        data["result_count"] = _safe_nonnegative_int(raw_data.get("result_count"))
        error_code = str(raw_data.get("error_code") or "")
        data["error_code"] = error_code if re.fullmatch(r"[A-Z0-9_]{1,64}", error_code) else None
    elif event_name == "support":
        data["iteration"] = _safe_nonnegative_int(raw_data.get("iteration"))
        data["supported_count"] = _safe_nonnegative_int(raw_data.get("supported_count"))
    elif event_name == "reflection":
        data["iteration"] = _safe_nonnegative_int(raw_data.get("iteration"))
        data["has_enough_information"] = bool(raw_data.get("has_enough_information"))
    return {
        "version": 1,
        "request_id": _safe_request_id(envelope.get("request_id")),
        "sequence": _safe_nonnegative_int(envelope.get("sequence")),
        "event": event_name,
        "data": data,
    }


def build_conversation_history(conversation: Conversation) -> list[dict]:
    """Build bounded history without trusting failed or weakly grounded answers."""
    history: list[dict] = []
    for message in conversation.messages:
        content = message.content.strip()
        if message.status != "succeeded" or not content:
            continue
        if message.role == "assistant":
            if message.answer_state not in {"grounded", "fully_grounded"}:
                continue
            history.append(
                {
                    "role": "assistant",
                    "content": content[:1200],
                    "grounded": True,
                }
            )
        elif message.role == "user":
            history.append(
                {
                    "role": "user",
                    "content": content[:1200],
                    "grounded": False,
                }
            )
    return history[-12:]


def _grounding_evidence(trace: dict) -> list[tuple[dict, str | None]]:
    grounding = trace.get("grounding") if isinstance(trace, dict) else None
    if isinstance(grounding, dict) and grounding.get("version") == 1:
        evidence = grounding.get("evidence")
        if isinstance(evidence, list):
            return [
                (item, _safe_identifier(item.get("evidence_id"), max_length=16))
                for item in evidence[:MAX_PERSISTED_CITATIONS]
                if isinstance(item, dict) and item.get("supported")
            ]
    documents: list[tuple[dict, str | None]] = []
    iterations = trace.get("iterations", []) if isinstance(trace, dict) else []
    if not isinstance(iterations, list):
        return documents
    for iteration in iterations[:10]:
        if not isinstance(iteration, dict):
            continue
        retrieved = iteration.get("retrieved_documents", [])
        if not isinstance(retrieved, list):
            continue
        for item in retrieved[:5]:
            if isinstance(item, dict) and item.get("supported"):
                documents.append((item, None))
    return documents


def _collect_supported_citations(
    session: Session,
    *,
    message: Message,
    trace: dict,
) -> tuple[list[Citation], dict[str, int]]:
    citations: list[Citation] = []
    evidence_to_citation: dict[str, int] = {}
    seen: dict[tuple, Citation] = {}
    for item, evidence_id in _grounding_evidence(trace):
        if len(citations) >= MAX_PERSISTED_CITATIONS:
            break
        if not isinstance(item, dict):
            continue
        if not item.get("supported"):
            continue
        source_type = "web" if item.get("source_type") == "web" else "knowledge_base"
        web_source = canonical_public_url(item.get("source_url")) if source_type == "web" else None
        source_identifier = _safe_identifier(item.get("document_id"))
        page_number = _safe_optional_int(item.get("page_number"), minimum=1)
        chunk_index = _safe_optional_int(item.get("chunk_index"))
        location_id = _safe_identifier(item.get("location_id"))
        evidence_text = _safe_text(item.get("text"), max_length=MAX_GROUNDING_EVIDENCE_TEXT) or ""
        temporal_metadata = extract_document_temporal_metadata(item)
        version_metadata = extract_document_version_metadata(item)
        key = (
            web_source[0] if web_source else None,
            location_id,
            source_identifier,
            page_number,
            chunk_index,
            evidence_text,
            tuple(temporal_metadata.items()),
            tuple(version_metadata.items()),
        )
        if key in seen:
            if evidence_id:
                evidence_to_citation[evidence_id] = seen[key].index
            continue
        source_document = None
        if source_identifier:
            source_document = session.scalar(
                select(Document).where(
                    Document.knowledge_base_id == message.conversation.knowledge_base_id,
                    (Document.id == source_identifier) | (Document.sha256 == source_identifier),
                )
            )
        display_name = _safe_text(
            (
                source_document.display_name
                if source_document
                else item.get("display_name") or item.get("reference") or "未知来源"
            ),
            max_length=255,
        )
        char_start = _safe_optional_int(item.get("char_start"))
        char_end = _safe_optional_int(item.get("char_end"))
        if char_start is not None and char_end is not None and char_end < char_start:
            char_start = None
            char_end = None
        citation = Citation(
            message_id=message.id,
            document_id=source_document.id if source_document else None,
            index=len(citations) + 1,
            display_name=display_name or "未知来源",
            page_number=page_number,
            chunk_index=chunk_index,
            section_title=_safe_text(item.get("section_title"), max_length=255),
            section_path=_safe_string_list(item.get("section_path")),
            char_start=char_start,
            char_end=char_end,
            bbox=_safe_bbox(item.get("bbox")),
            location_id=location_id,
            source_locator=_safe_text(item.get("source_locator"), max_length=128),
            parser_version=_safe_text(item.get("parser_version"), max_length=128),
            extraction_method=_safe_text(item.get("extraction_method"), max_length=32),
            source_type=source_type,
            source_url=web_source[0] if web_source else None,
            source_domain=web_source[1] if web_source else None,
            trusted=(
                bool(item.get("trusted", False))
                if source_type == "web" and web_source
                else source_type != "web"
            ),
            published_at=(
                date.fromisoformat(temporal_metadata["published_at"])
                if "published_at" in temporal_metadata
                else None
            ),
            effective_at=(
                date.fromisoformat(temporal_metadata["effective_at"])
                if "effective_at" in temporal_metadata
                else None
            ),
            superseded_at=(
                date.fromisoformat(temporal_metadata["superseded_at"])
                if "superseded_at" in temporal_metadata
                else None
            ),
            temporal_metadata_source=temporal_metadata.get("temporal_metadata_source"),
            version_family=version_metadata.get("version_family"),
            version_family_source=version_metadata.get("version_family_source"),
            text=evidence_text,
            supported=True,
        )
        session.add(citation)
        citations.append(citation)
        seen[key] = citation
        if evidence_id:
            evidence_to_citation[evidence_id] = citation.index
    return citations, evidence_to_citation


def collect_supported_citations(
    session: Session,
    *,
    message: Message,
    trace: dict,
) -> list[Citation]:
    citations, _ = _collect_supported_citations(
        session,
        message=message,
        trace=trace,
    )
    return citations


def collect_answer_claims(
    session: Session,
    *,
    message: Message,
    trace: dict,
    evidence_to_citation: dict[str, int],
) -> list[AnswerClaim]:
    trust = trace.get("trust") if isinstance(trace, dict) else None
    grounding = trace.get("grounding") if isinstance(trace, dict) else None
    raw_claims = (
        trust.get("claims")
        if isinstance(trust, dict) and trust.get("version") == 1
        else grounding.get("claims")
        if isinstance(grounding, dict)
        else None
    )
    if not isinstance(raw_claims, list):
        return []
    evidence_text_by_id: dict[str, str] = {}
    if isinstance(grounding, dict) and grounding.get("version") == 1:
        raw_evidence = grounding.get("evidence")
        if isinstance(raw_evidence, list):
            for evidence_item in raw_evidence[:MAX_PERSISTED_CITATIONS]:
                if not isinstance(evidence_item, dict):
                    continue
                evidence_id = _safe_identifier(evidence_item.get("evidence_id"), max_length=16)
                evidence_text = _safe_text(
                    evidence_item.get("text"),
                    max_length=MAX_GROUNDING_EVIDENCE_TEXT,
                )
                if evidence_id is not None and evidence_text is not None:
                    evidence_text_by_id[evidence_id] = evidence_text
    claims: list[AnswerClaim] = []
    allowed_statuses = {"supported", "unsupported", "invalid_citation", "conflicting"}
    for item in raw_claims[:64]:
        if not isinstance(item, dict):
            continue
        text = _safe_text(item.get("text"), max_length=600)
        status = str(item.get("support_status") or item.get("status") or "unsupported")
        if not text or status not in allowed_statuses:
            continue
        raw_evidence_ids = item.get("evidence_ids")
        evidence_ids = raw_evidence_ids if isinstance(raw_evidence_ids, list) else []
        citation_indices = list(
            dict.fromkeys(
                evidence_to_citation[evidence_id]
                for raw_evidence_id in evidence_ids[:20]
                if (evidence_id := _safe_identifier(raw_evidence_id, max_length=16))
                in evidence_to_citation
            )
        )
        required_citations = 2 if status == "conflicting" else 1
        if status in {"supported", "conflicting"} and len(citation_indices) < required_citations:
            status = "invalid_citation"
        default_citation_status = {
            "supported": "valid",
            "conflicting": "conflicting",
            "invalid_citation": "invalid",
            "unsupported": "missing",
        }[status]
        default_reason_code = {
            "supported": "CITATION_VALID",
            "conflicting": "EVIDENCE_CONFLICT",
            "invalid_citation": "CITATION_INVALID",
            "unsupported": "CITATION_MISSING",
        }[status]
        claim = AnswerClaim(
            message_id=message.id,
            index=len(claims) + 1,
            text=text,
            support_status=status,
            structural_support_status=(
                _safe_identifier(item.get("structural_support_status"), max_length=32) or status
            ),
            citation_indices=citation_indices,
            citation_spans=_safe_citation_spans(
                item.get("citation_spans"),
                evidence_to_citation=evidence_to_citation,
                evidence_text_by_id=evidence_text_by_id,
            ),
            citation_status=(
                _safe_identifier(item.get("citation_status"), max_length=24)
                or default_citation_status
            ),
            entailment_status=(
                _safe_identifier(item.get("entailment_status"), max_length=24) or "not_checked"
            ),
            consistency_status=(
                _safe_identifier(item.get("consistency_status"), max_length=24) or "not_checked"
            ),
            consistency_checks=_safe_consistency_checks(item.get("consistency_checks")),
            risk_status=(
                _safe_identifier(item.get("risk_status"), max_length=24) or "not_assessed"
            ),
            risk_checks=_safe_risk_checks(item.get("risk_checks")),
            confidence=_safe_confidence(item.get("confidence")),
            reason_codes=(_safe_reason_codes(item.get("reason_codes")) or [default_reason_code]),
        )
        session.add(claim)
        claims.append(claim)
    return claims


async def submit_message(
    session: Session,
    *,
    conversation: Conversation,
    content: str,
    request_id: str | None = None,
    use_web_search: bool = False,
) -> tuple[Message, Message]:
    question = content.strip()
    if not question:
        raise ProductError("MESSAGE_EMPTY", "请输入你想了解的问题。")

    conversation_history = build_conversation_history(conversation)
    user_message, assistant_message = _create_pending_messages(
        session,
        conversation=conversation,
        question=question,
    )

    try:
        async with httpx.AsyncClient(
            timeout=300.0,
            trust_env=False,
            headers=backend_request_headers(request_id),
        ) as client:
            response = await client.post(
                f"{BACKEND_URL}/query",
                json={
                    "original_query": question,
                    "conversation_history": conversation_history,
                    "max_iter": 3,
                    "include_trace": True,
                    "collection_names": [conversation.knowledge_base.collection_name],
                    "use_web_search": use_web_search,
                },
            )
        if not response.is_success:
            raise query_error_from_response(response)
        payload = response.json()
        _finish_assistant_message(
            session,
            assistant_message=assistant_message,
            payload=payload,
        )
        return user_message, assistant_message
    except (httpx.RequestError, ValueError, ProductError) as exc:
        _fail_assistant_message(
            session,
            assistant_message=assistant_message,
            message=(
                exc.message if isinstance(exc, ProductError) else "问答服务暂时不可用，请稍后重试。"
            ),
        )
        if isinstance(exc, ProductError):
            raise
        raise ProductError(
            "QUERY_UNAVAILABLE",
            "问答服务暂时不可用，请稍后重试。",
            status_code=503,
            retryable=True,
        ) from exc


async def stream_message_events(
    session: Session,
    *,
    conversation: Conversation,
    content: str,
    request_id: str | None = None,
    use_web_search: bool = False,
) -> AsyncIterator[dict]:
    """Consume core SSE, persist only the final answer/citations, and relay safe stages."""
    question = content.strip()
    if not question:
        raise ProductError("MESSAGE_EMPTY", "请输入你想了解的问题。")

    conversation_history = build_conversation_history(conversation)
    user_message, assistant_message = _create_pending_messages(
        session,
        conversation=conversation,
        question=question,
    )
    terminal_emitted = False
    expected_event_request_id = _safe_request_id(request_id)
    last_sequence = 0
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0),
            trust_env=False,
            headers=backend_request_headers(request_id),
        ) as client:
            async with client.stream(
                "POST",
                f"{BACKEND_URL}/query/stream",
                json={
                    "original_query": question,
                    "conversation_history": conversation_history,
                    "max_iter": 3,
                    "collection_names": [conversation.knowledge_base.collection_name],
                    "use_web_search": use_web_search,
                },
            ) as response:
                if not response.is_success:
                    await response.aread()
                    raise query_error_from_response(response)
                async for event_name, envelope in _iter_sse_events(response):
                    event_request_id, last_sequence = _validate_stream_envelope(
                        event_name,
                        envelope,
                        expected_request_id=expected_event_request_id,
                        last_sequence=last_sequence,
                    )
                    if not expected_event_request_id:
                        expected_event_request_id = event_request_id
                    safe_stage = _safe_stage_envelope(event_name, envelope)
                    if safe_stage is not None:
                        yield safe_stage
                        continue
                    data = envelope.get("data") if isinstance(envelope, dict) else {}
                    if not isinstance(data, dict):
                        data = {}
                    if event_name == "completed":
                        _finish_assistant_message(
                            session,
                            assistant_message=assistant_message,
                            payload=data,
                        )
                        terminal_emitted = True
                        yield {
                            "version": 1,
                            "request_id": _safe_request_id(envelope.get("request_id")),
                            "sequence": _safe_nonnegative_int(envelope.get("sequence")),
                            "event": "completed",
                            "data": {
                                "user_message": _serialize_message(user_message),
                                "assistant_message": _serialize_message(assistant_message),
                            },
                        }
                        return
                    if event_name == "error":
                        raise query_error_from_event(data)
                    if event_name == "cancelled":
                        raise ProductError(
                            "QUERY_CANCELLED",
                            "本次回答已停止。",
                            status_code=499,
                            retryable=True,
                        )
                raise ProductError(
                    "QUERY_STREAM_INCOMPLETE",
                    "问答服务连接提前结束，请重新尝试。",
                    status_code=502,
                    retryable=True,
                )
    except asyncio.CancelledError:
        _fail_assistant_message(
            session,
            assistant_message=assistant_message,
            message="本次回答已停止。",
        )
        raise
    except (httpx.RequestError, ValueError, ProductError) as exc:
        product_error = (
            exc
            if isinstance(exc, ProductError)
            else ProductError(
                "QUERY_UNAVAILABLE",
                "问答服务暂时不可用，请稍后重试。",
                status_code=503,
                retryable=True,
            )
        )
        _fail_assistant_message(
            session,
            assistant_message=assistant_message,
            message=product_error.message,
        )
        terminal_emitted = True
        yield {
            "version": 1,
            "request_id": "",
            "sequence": 0,
            "event": "error",
            "data": {
                "code": product_error.code,
                "message": product_error.message,
                "retryable": product_error.retryable,
            },
        }
    finally:
        if not terminal_emitted and assistant_message.status == "pending":
            _fail_assistant_message(
                session,
                assistant_message=assistant_message,
                message="本次回答已停止。",
            )
