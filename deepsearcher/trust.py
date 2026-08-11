"""Versioned trust contract and deterministic answer policy for RAG responses.

This release combines citation-structure validation with bounded deterministic
checks for quantities, dates, versions, ranges, negation and restrictive
conditions.  An optional, strictly parsed NLI checker handles residual claims;
its unknown or failed results remain explicit and do not overwrite deterministic
findings or the policy boundary introduced here.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from deepsearcher.entailment import BaseEntailmentChecker, EntailmentInput
from deepsearcher.freshness import sanitize_freshness_intent
from deepsearcher.risk import default_risk_profile
from deepsearcher.temporal import extract_document_temporal_metadata, published_anchor
from deepsearcher.versioning import extract_document_version_metadata

TRUST_CONTRACT_VERSION = 1
ANSWER_POLICY_VERSION = 1
CONSISTENCY_CHECKER_VERSION = "1.6.0"
DEFAULT_POLICY_PROFILE = "standard"
DEFAULT_RISK_LEVEL = "medium"

INSUFFICIENT_EVIDENCE_ANSWER = "根据现有证据，无法给出有充分依据的回答。"

TRUST_STATES = {
    "fully_grounded",
    "partially_grounded",
    "conflicting_evidence",
    "insufficient_evidence",
    "not_assessed",
}

DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})"
    r"(?:\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?)?(?!\d)"
)
ABSOLUTE_QUARTER_PATTERN = re.compile(
    r"(?:(?<!\d)(?P<year>\d{4})\s*年?\s*(?:第\s*)?"
    r"(?P<quarter>[1-4一二三四])\s*季度"
    r"|(?<![A-Za-z0-9])(?P<iso_year>\d{4})\s*[-/]?\s*Q(?P<iso_quarter>[1-4])"
    r"(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
ABSOLUTE_WEEK_PATTERN = re.compile(
    r"(?:(?<!\d)(?P<year>\d{4})\s*年\s*第?\s*(?P<week>\d{1,2})\s*周"
    r"|(?<![A-Za-z0-9])(?P<iso_year>\d{4})\s*-?W(?P<iso_week>\d{1,2})"
    r"(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
ABSOLUTE_YEAR_PATTERN = re.compile(r"(?<!\d)(?P<year>\d{4})\s*年(?:度)?(?!\s*\d{1,2}\s*月)")
RELATIVE_TIME_PATTERNS = (
    ("day:-2", re.compile(r"前天|(?i:\bday\s+before\s+yesterday\b)")),
    ("day:-1", re.compile(r"昨天|昨日|(?i:\byesterday\b)")),
    ("day:0", re.compile(r"今天|今日|(?i:\btoday\b)")),
    ("day:1", re.compile(r"明天|明日|(?i:\btomorrow\b)")),
    ("day:2", re.compile(r"后天|(?i:\bday\s+after\s+tomorrow\b)")),
    ("week:-1", re.compile(r"上周|上一周|(?i:\blast\s+week\b)")),
    ("week:0", re.compile(r"本周|这周|这一周|(?i:\bthis\s+week\b)")),
    ("week:1", re.compile(r"下周|下一周|(?i:\bnext\s+week\b)")),
    ("month:-1", re.compile(r"上月|上个月|(?i:\blast\s+month\b)")),
    ("month:0", re.compile(r"本月|这个月|(?i:\bthis\s+month\b)")),
    ("month:1", re.compile(r"下月|下个月|(?i:\bnext\s+month\b)")),
    ("quarter:-1", re.compile(r"上季度|上一季度|(?i:\blast\s+quarter\b)")),
    ("quarter:0", re.compile(r"本季度|这个季度|(?i:\bthis\s+quarter\b)")),
    ("quarter:1", re.compile(r"下季度|下一季度|(?i:\bnext\s+quarter\b)")),
    ("year:-1", re.compile(r"去年|上一年|(?i:\blast\s+year\b)")),
    ("year:0", re.compile(r"今年|本年|(?i:\bthis\s+year\b)")),
    ("year:1", re.compile(r"明年|下一年|(?i:\bnext\s+year\b)")),
)
VERSION_PATTERN = re.compile(
    r"(?i)(?:(?:version|版本)\s*[:：]?\s*|(?<![A-Za-z0-9])v)"
    r"(?P<labelled>\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?)"
    r"|(?<![\d.])(?P<bare>\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?)(?![\d.])"
)
QUANTITY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<number>[+-]?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>KiB|MiB|GiB|TiB|KB|MB|GB|TB|%|％|页|个|次|条|份|"
    r"年|月|日|秒|分钟|小时|元|万元|亿元|倍|轮|维|字|字符|tokens?)?",
    re.IGNORECASE,
)
RANGE_PATTERN = re.compile(
    r"(?P<operator>不超过|不高于|小于等于|至多|最多|最大|≤|<=|"
    r"不少于|不低于|大于等于|至少|最少|最小|≥|>=|"
    r"小于|少于|低于|<|大于|多于|高于|超过|>)\s*"
    r"(?:为|是|保存|支持|允许|限制为|可达)?\s*"
    r"(?P<number>[+-]?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>KiB|MiB|GiB|TiB|KB|MB|GB|TB|%|％|页|个|次|条|份|"
    r"年|月|日|秒|分钟|小时|元|万元|亿元|倍|轮|维|字|字符|tokens?)?",
    re.IGNORECASE,
)
NEGATION_PATTERN = re.compile(r"不得|不能|禁止|没有|并非|不是|拒绝|不|未|无")
CLAUSE_SPLIT = re.compile(r"(?:[。！？!?；;，,]|但是|但|同时|而)")
FACT_CLAUSE_SPLIT = re.compile(r"[\n。！？!?；;，,]+")
PUNCTUATION_OR_SPACE = re.compile(r"[\s\W_]+", re.UNICODE)
ENTITY_PATTERNS = (
    ("pdf_file", re.compile(r"(?i)\bpdfs?\b|文件|文档")),
    ("image", re.compile(r"(?i)\bimages?\b|图片|图像")),
    ("knowledge_base", re.compile(r"(?i)\bknowledge\s*bases?\b|知识库")),
    ("organization", re.compile(r"(?i)\borganizations?\b|组织")),
    ("workspace", re.compile(r"(?i)\bworkspaces?\b|工作区")),
    ("user", re.compile(r"(?i)\busers?\b|用户|成员")),
    ("request", re.compile(r"(?i)\brequests?\b|请求")),
    ("query", re.compile(r"(?i)\bqueries?\b|查询")),
    ("chunk", re.compile(r"(?i)\bchunks?\b|分块|片段")),
    ("page", re.compile(r"(?i)\bpages?\b|页面|页数")),
    ("model", re.compile(r"(?i)\bmodels?\b|模型")),
    ("index", re.compile(r"(?i)\bindexes?\b|\bindices\b|索引")),
)
RESTRICTION_PATTERNS = (
    re.compile(
        r"只有(?P<condition>[^，。；;]{1,30}?)(?:才|才能|方可|可以|可|能|能够)"
        r"(?P<conclusion>[^，。；;]{2,60})"
    ),
    re.compile(
        r"仅(?P<condition>[^，。；;]{1,24}?)(?:可以|可|能|能够|允许)"
        r"(?P<conclusion>[^，。；;]{2,60})"
    ),
    re.compile(
        r"必须(?P<condition>[^，。；;]{1,30}?)(?:后)?(?:才|才能|方可|可以|可|能)"
        r"(?P<conclusion>[^，。；;]{2,60})"
    ),
)
UNLESS_RESTRICTION_PATTERN = re.compile(
    r"除非(?P<condition>[^，。；;]{1,40}?)[，,]?否则"
    r"(?:不得|不能|禁止|不允许)(?P<conclusion>[^，。；;]{2,60})"
)
GENERAL_CONDITION_PATTERN = re.compile(
    r"(?:^|[。！？!?；;，,])(?P<condition>[^。！？!?；;，,]{2,50}?)"
    r"(?:后|之后)?(?:均可|都可|即可|方可|才可|才能|可以|可|能够|能|允许)"
    r"(?P<conclusion>[^，。；;]{2,60})"
)
ANY_CONDITION_CONNECTOR = re.compile(
    r"(?:或者|或|任一(?:条件|角色)?|任意(?:一个)?(?:条件|角色)?|\bor\b|/)"
)
ALL_CONDITION_CONNECTOR = re.compile(r"(?:并且|并|以及|和|与|及|、|同时|\band\b)")
NEGATED_CONDITION_PATTERN = re.compile(r"无需|无须|不需|不必|免于|未完成|未通过|未获得")
CONDITION_PREFIX_NOISE = re.compile(
    r"^(?:只有|仅|必须|需要|需|满足|同时满足|经|已|先|完成|通过|获得|取得|由)+"
)
CONDITION_SUFFIX_NOISE = re.compile(
    r"(?:之后|以后|后|时|的情况下|任一角色|任一条件|任意一个条件|条件之一|角色之一|条件|角色|之一|均|都)+$"
)
MODAL_PATTERN = re.compile(r"才能|能够|可以|允许|方可|才|可|能")
RANGE_OPERATOR_ALIASES = {
    "不超过": "lte",
    "不高于": "lte",
    "小于等于": "lte",
    "至多": "lte",
    "最多": "lte",
    "最大": "lte",
    "≤": "lte",
    "<=": "lte",
    "不少于": "gte",
    "不低于": "gte",
    "大于等于": "gte",
    "至少": "gte",
    "最少": "gte",
    "最小": "gte",
    "≥": "gte",
    ">=": "gte",
    "小于": "lt",
    "少于": "lt",
    "低于": "lt",
    "<": "lt",
    "大于": "gt",
    "多于": "gt",
    "高于": "gt",
    "超过": "gt",
    ">": "gt",
}


def _claims(grounding: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_claims = grounding.get("claims")
    if not isinstance(raw_claims, list):
        return []
    return [claim for claim in raw_claims if isinstance(claim, dict)]


def _canonical_number(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _canonical_unit(value: str | None) -> str:
    unit = str(value or "").strip().lower()
    aliases = {"％": "%", "token": "tokens"}
    return aliases.get(unit, unit)


def temporal_timezone_from_query_settings(query_settings: Mapping[str, Any] | None) -> str:
    """Read and validate the server-owned timezone used by relative-time checks."""

    settings = query_settings if isinstance(query_settings, Mapping) else {}
    trust = settings.get("trust")
    trust = trust if isinstance(trust, Mapping) else {}
    temporal = trust.get("temporal")
    temporal = temporal if isinstance(temporal, Mapping) else {}
    timezone_name = str(temporal.get("timezone") or "UTC").strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid trust temporal timezone: {timezone_name}") from exc
    return timezone_name


def build_temporal_context(
    *,
    reference_time: datetime | str | None = None,
    timezone_name: str = "UTC",
) -> dict[str, Any]:
    """Build an auditable request clock without relying on process-local timezones."""

    try:
        local_zone = ZoneInfo(str(timezone_name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"invalid trust temporal timezone: {timezone_name}") from exc
    if reference_time is None:
        instant = datetime.now(timezone.utc)
    elif isinstance(reference_time, datetime):
        instant = reference_time
    else:
        raw = str(reference_time).strip().replace("Z", "+00:00")
        try:
            instant = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("invalid trust temporal reference_time") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("trust temporal reference_time must include an offset")
    utc_instant = instant.astimezone(timezone.utc)
    local_instant = utc_instant.astimezone(local_zone)
    return {
        "version": 1,
        "source": "request_clock",
        "reference_time": utc_instant.isoformat(timespec="seconds"),
        "reference_date": local_instant.date().isoformat(),
        "timezone": str(local_zone.key),
    }


def _temporal_reference_date(context: Mapping[str, Any] | None) -> date | None:
    if not isinstance(context, Mapping) or context.get("version") != 1:
        return None
    try:
        zone = ZoneInfo(str(context.get("timezone") or ""))
        instant = datetime.fromisoformat(
            str(context.get("reference_time") or "").replace("Z", "+00:00")
        )
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        resolved = instant.astimezone(zone).date()
        stated = date.fromisoformat(str(context.get("reference_date") or ""))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None
    return resolved if resolved == stated else None


def _extract_relative_time_terms(text: str) -> tuple[str, ...]:
    candidates = [
        (match.start(), match.end(), signature)
        for signature, pattern in RELATIVE_TIME_PATTERNS
        for match in pattern.finditer(str(text or ""))
    ]
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str]] = []
    for start, end, signature in candidates:
        if any(
            start < selected_end and end > selected_start
            for selected_start, selected_end, _ in selected
        ):
            continue
        selected.append((start, end, signature))
    return tuple(sorted({signature for _, _, signature in selected}))


def _shift_month(value: date, months: int) -> tuple[int, int]:
    index = value.year * 12 + value.month - 1 + months
    return index // 12, index % 12 + 1


def _resolve_relative_time_terms(terms: tuple[str, ...], reference: date) -> set[str]:
    values: set[str] = set()
    for term in terms:
        kind, raw_offset = term.split(":", 1)
        offset = int(raw_offset)
        if kind == "day":
            values.add(f"date:{(reference + timedelta(days=offset)).isoformat()}")
        elif kind == "week":
            target = reference + timedelta(weeks=offset)
            iso_year, iso_week, _ = target.isocalendar()
            values.add(f"week:{iso_year:04d}-W{iso_week:02d}")
        elif kind == "month":
            year, month = _shift_month(reference, offset)
            values.add(f"month:{year:04d}-{month:02d}")
        elif kind == "quarter":
            quarter_index = reference.year * 4 + (reference.month - 1) // 3 + offset
            values.add(f"quarter:{quarter_index // 4:04d}-Q{quarter_index % 4 + 1}")
        elif kind == "year":
            values.add(f"year:{reference.year + offset:04d}")
    return values


def _containing_temporal_values(value: date) -> set[str]:
    iso_year, iso_week, _ = value.isocalendar()
    quarter = (value.month - 1) // 3 + 1
    return {
        f"date:{value.isoformat()}",
        f"week:{iso_year:04d}-W{iso_week:02d}",
        f"month:{value.year:04d}-{value.month:02d}",
        f"quarter:{value.year:04d}-Q{quarter}",
        f"year:{value.year:04d}",
    }


def _extract_absolute_temporal_values(text: str) -> set[str]:
    raw = str(text or "")
    values: set[str] = set()
    dates, _ = _extract_dates(raw)
    for item in dates:
        if len(item) == 10:
            try:
                values.update(_containing_temporal_values(date.fromisoformat(item)))
            except ValueError:
                continue
        elif len(item) == 7:
            year, month = (int(part) for part in item.split("-", 1))
            values.add(f"month:{item}")
            values.add(f"quarter:{year:04d}-Q{(month - 1) // 3 + 1}")
            values.add(f"year:{year:04d}")
    for match in ABSOLUTE_QUARTER_PATTERN.finditer(raw):
        year = int(match.group("year") or match.group("iso_year"))
        raw_quarter = match.group("quarter") or match.group("iso_quarter")
        quarter = {"一": 1, "二": 2, "三": 3, "四": 4}.get(
            str(raw_quarter), int(raw_quarter) if str(raw_quarter).isdigit() else 0
        )
        if quarter:
            values.add(f"quarter:{year:04d}-Q{quarter}")
            values.add(f"year:{year:04d}")
    for match in ABSOLUTE_WEEK_PATTERN.finditer(raw):
        year = int(match.group("year") or match.group("iso_year"))
        week = int(match.group("week") or match.group("iso_week"))
        try:
            date.fromisocalendar(year, week, 1)
        except ValueError:
            continue
        values.add(f"week:{year:04d}-W{week:02d}")
        values.add(f"year:{year:04d}")
    for match in ABSOLUTE_YEAR_PATTERN.finditer(raw):
        values.add(f"year:{int(match.group('year')):04d}")
    return values


def _evidence_temporal_values(item: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    text = str(item.get("text") or "")
    values = _extract_absolute_temporal_values(text)
    relative_terms = set(_extract_relative_time_terms(text))
    anchor = published_anchor(extract_document_temporal_metadata(item))
    if anchor is not None and relative_terms:
        values.update(_resolve_relative_time_terms(tuple(relative_terms), anchor))
        relative_terms.clear()
    return values, relative_terms


def _relative_time_entity_mismatches(
    claim_text: str,
    evidence_items: list[Mapping[str, Any]],
    claim_values: set[str],
) -> list[str]:
    claim_entities = _extract_fact_entities(claim_text)
    if not claim_entities:
        return []
    mismatches: set[str] = set()
    for value in claim_values:
        evidence_entity_sets = [
            _extract_fact_entities(clause)
            for evidence_item in evidence_items
            for clause in FACT_CLAUSE_SPLIT.split(str(evidence_item.get("text") or ""))
            if value
            in _evidence_temporal_values(
                {
                    **evidence_item,
                    "text": clause,
                }
            )[0]
        ]
        if evidence_entity_sets and not any(
            not evidence_entities or claim_entities.issubset(evidence_entities)
            for evidence_entities in evidence_entity_sets
        ):
            mismatches.add(value)
    return sorted(mismatches)


def _extract_dates(text: str) -> tuple[set[str], str]:
    dates: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        year = int(match.group("year"))
        month = int(match.group("month"))
        raw_day = match.group("day")
        if not 1 <= month <= 12:
            return match.group(0)
        if raw_day is None:
            dates.add(f"{year:04d}-{month:02d}")
            return " "
        day = int(raw_day)
        if not 1 <= day <= 31:
            return match.group(0)
        dates.add(f"{year:04d}-{month:02d}-{day:02d}")
        return " "

    remaining = DATE_PATTERN.sub(replace, str(text or ""))
    return dates, remaining


def _extract_versions(text: str) -> tuple[set[str], str]:
    versions: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        value = match.group("labelled") or match.group("bare")
        if value:
            versions.add(value.lower())
        return " "

    remaining = VERSION_PATTERN.sub(replace, str(text or ""))
    return versions, remaining


def _extract_quantities(text: str) -> set[str]:
    _, remaining = _extract_dates(text)
    _, remaining = _extract_versions(remaining)
    quantities: set[str] = set()
    for match in QUANTITY_PATTERN.finditer(remaining):
        number = _canonical_number(match.group("number"))
        unit = _canonical_unit(match.group("unit"))
        quantities.add(f"{number}|{unit}")
    return quantities


def _extract_ranges(text: str) -> set[str]:
    ranges: set[str] = set()
    for match in RANGE_PATTERN.finditer(str(text or "")):
        operator = RANGE_OPERATOR_ALIASES.get(match.group("operator"), "")
        number = _canonical_number(match.group("number"))
        unit = _canonical_unit(match.group("unit"))
        if operator:
            ranges.add(f"{operator}|{number}|{unit}")
    return ranges


def _extract_fact_entities(text: str) -> frozenset[str]:
    return frozenset(
        entity for entity, pattern in ENTITY_PATTERNS if pattern.search(str(text or ""))
    )


def _fact_entity_map(
    texts: list[str],
    extractor,
) -> dict[str, list[frozenset[str]]]:
    facts: dict[str, list[frozenset[str]]] = {}
    for text in texts:
        for clause in FACT_CLAUSE_SPLIT.split(str(text or "")):
            values = extractor(clause)
            if not values:
                continue
            entities = _extract_fact_entities(clause)
            for value in values:
                facts.setdefault(value, []).append(entities)
    return facts


def _fact_entity_mismatches(
    claim_text: str,
    evidence_texts: list[str],
    extractor,
) -> list[str]:
    claim_facts = _fact_entity_map([claim_text], extractor)
    evidence_facts = _fact_entity_map(evidence_texts, extractor)
    mismatches: set[str] = set()
    for value, claim_entity_sets in claim_facts.items():
        evidence_entity_sets = evidence_facts.get(value, [])
        if not evidence_entity_sets:
            continue
        for claim_entities in claim_entity_sets:
            if not claim_entities:
                continue
            # An unclassified evidence clause remains a conservative fallback:
            # deterministic rules must not invent an entity mismatch.
            if any(
                not evidence_entities or claim_entities.issubset(evidence_entities)
                for evidence_entities in evidence_entity_sets
            ):
                continue
            mismatches.add(value)
    return sorted(mismatches)


def _normalize_clause(value: str, *, remove_modals: bool = False) -> str:
    text = MODAL_PATTERN.sub("", value) if remove_modals else value
    return PUNCTUATION_OR_SPACE.sub("", text).lower()


def _normalize_condition_term(value: str) -> str:
    term = CONDITION_PREFIX_NOISE.sub("", str(value or "").strip())
    term = CONDITION_SUFFIX_NOISE.sub("", term)
    return _normalize_clause(term, remove_modals=True)


def _condition_expression(value: str) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    negated = NEGATED_CONDITION_PATTERN.search(raw) is not None
    without_negation = NEGATED_CONDITION_PATTERN.sub("", raw)
    has_any = ANY_CONDITION_CONNECTOR.search(without_negation) is not None
    has_all = ALL_CONDITION_CONNECTOR.search(without_negation) is not None
    if has_any and has_all:
        relation = "complex"
        raw_terms = [without_negation]
    elif has_any:
        relation = "any"
        raw_terms = ANY_CONDITION_CONNECTOR.split(without_negation)
    elif has_all:
        relation = "all"
        raw_terms = ALL_CONDITION_CONNECTOR.split(without_negation)
    else:
        relation = "single"
        raw_terms = [without_negation]
    terms = sorted(
        {
            term
            for raw_term in raw_terms
            if (term := _normalize_condition_term(raw_term)) and len(term) >= 2
        }
    )
    if not terms:
        return None
    if len(terms) == 1 and relation in {"all", "any"}:
        relation = "single"
    if relation == "single":
        signature = terms[0]
    else:
        signature = f"{relation}({','.join(terms)})"
    if negated:
        signature = f"negated({signature})"
    return {
        "relation": relation,
        "terms": tuple(terms),
        "negated": negated,
        "signature": signature,
    }


def _restriction(
    condition: str,
    conclusion: str,
    *,
    source: str,
) -> dict[str, Any] | None:
    expression = _condition_expression(condition)
    normalized_conclusion = _normalize_clause(conclusion, remove_modals=True)
    if expression is None or len(normalized_conclusion) < 4:
        return None
    return {
        **expression,
        "condition": expression["signature"],
        "conclusion": normalized_conclusion,
        "source": source,
    }


def _extract_restrictions(text: str) -> list[dict[str, Any]]:
    restrictions: list[dict[str, Any]] = []
    for pattern in RESTRICTION_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            parsed = _restriction(
                match.group("condition"),
                match.group("conclusion"),
                source="required",
            )
            if parsed is not None:
                restrictions.append(parsed)
    for match in UNLESS_RESTRICTION_PATTERN.finditer(str(text or "")):
        parsed = _restriction(
            match.group("condition"),
            match.group("conclusion"),
            source="unless",
        )
        if parsed is not None:
            restrictions.append(parsed)
    for match in GENERAL_CONDITION_PATTERN.finditer(str(text or "")):
        raw_condition = match.group("condition")
        if re.search(r"只有|仅|必须|除非|否则", raw_condition):
            continue
        parsed = _restriction(
            raw_condition,
            match.group("conclusion"),
            source="stated",
        )
        if parsed is None:
            continue
        if any(
            _conclusions_compatible(parsed["conclusion"], item["conclusion"])
            and parsed["condition"] == item["condition"]
            for item in restrictions
        ):
            continue
        restrictions.append(parsed)
    return restrictions


def _condition_expression_supported(
    evidence_condition: Mapping[str, Any],
    claim_condition: Mapping[str, Any],
) -> bool:
    if bool(claim_condition.get("negated")) != bool(evidence_condition.get("negated")):
        return False
    evidence_relation = str(evidence_condition.get("relation") or "single")
    claim_relation = str(claim_condition.get("relation") or "single")
    evidence_terms = set(evidence_condition.get("terms") or ())
    claim_terms = set(claim_condition.get("terms") or ())
    if not evidence_terms or not claim_terms:
        return False
    if "complex" in {evidence_relation, claim_relation}:
        return evidence_relation == claim_relation and evidence_terms == claim_terms
    if evidence_condition.get("source") == "stated":
        return all(
            any(required in candidate or candidate in required for required in evidence_terms)
            for candidate in claim_terms
        )

    def covers(required_terms: set[str], candidate_terms: set[str]) -> bool:
        return all(
            any(required in candidate or candidate in required for candidate in candidate_terms)
            for required in required_terms
        )

    def overlaps(left_terms: set[str], right_terms: set[str]) -> bool:
        return any(left in right or right in left for left in left_terms for right in right_terms)

    if evidence_relation == "all":
        return claim_relation in {"all", "single"} and covers(evidence_terms, claim_terms)
    if evidence_relation == "any":
        if claim_relation == "any":
            return covers(claim_terms, evidence_terms)
        if claim_relation == "all":
            return overlaps(claim_terms, evidence_terms)
        return covers(claim_terms, evidence_terms)
    if claim_relation == "any":
        return covers(claim_terms, evidence_terms)
    return covers(evidence_terms, claim_terms)


def _condition_term_present(term: str, claim_core: str) -> bool:
    negative_forms = (
        f"非{term}",
        f"不是{term}",
        f"无需{term}",
        f"无须{term}",
        f"不需{term}",
        f"不必{term}",
        f"未完成{term}",
        f"未通过{term}",
        f"未获得{term}",
    )
    return term in claim_core and not any(form in claim_core for form in negative_forms)


def _condition_present_in_claim(
    evidence_condition: Mapping[str, Any],
    claim_core: str,
) -> bool:
    terms = tuple(str(item) for item in evidence_condition.get("terms") or ())
    relation = str(evidence_condition.get("relation") or "single")
    if bool(evidence_condition.get("negated")) or not terms:
        return False
    present = [_condition_term_present(term, claim_core) for term in terms]
    return any(present) if relation == "any" else all(present)


def _conclusions_compatible(left: str, right: str) -> bool:
    return left in right or right in left


def _check_conditions(claim_text: str, evidence_texts: list[str]) -> dict[str, Any] | None:
    claim_core = _normalize_clause(claim_text, remove_modals=True)
    claim_restrictions = _extract_restrictions(claim_text)
    evidence_restrictions = [
        restriction
        for evidence_text in evidence_texts
        for restriction in _extract_restrictions(evidence_text)
    ]
    relevant_evidence = [
        restriction
        for restriction in evidence_restrictions
        if restriction["conclusion"] in claim_core
        or any(
            _conclusions_compatible(restriction["conclusion"], claim_restriction["conclusion"])
            for claim_restriction in claim_restrictions
        )
    ]
    relevant_claims = [
        restriction
        for restriction in claim_restrictions
        if any(
            _conclusions_compatible(restriction["conclusion"], evidence_restriction["conclusion"])
            for evidence_restriction in evidence_restrictions
        )
    ]
    if not relevant_evidence and not relevant_claims:
        return None

    missing_conditions: list[str] = []
    relation_mismatch = False
    negated_condition = False
    for evidence_restriction in relevant_evidence:
        condition_present = _condition_present_in_claim(evidence_restriction, claim_core) or any(
            _condition_expression_supported(evidence_restriction, claim_restriction)
            for claim_restriction in relevant_claims
        )
        if not condition_present:
            missing_conditions.append(evidence_restriction["condition"])
            for claim_restriction in relevant_claims:
                if set(evidence_restriction["terms"]) & set(claim_restriction["terms"]):
                    relation_mismatch = relation_mismatch or (
                        evidence_restriction["relation"] != claim_restriction["relation"]
                    )
                    negated_condition = negated_condition or bool(
                        claim_restriction.get("negated")
                    ) != bool(evidence_restriction.get("negated"))

    unsupported_claim_restrictions = [
        claim_restriction
        for claim_restriction in relevant_claims
        if not any(
            _condition_expression_supported(evidence_restriction, claim_restriction)
            for evidence_restriction in relevant_evidence
        )
    ]
    unsupported_claim_conditions = [
        claim_restriction["condition"] for claim_restriction in unsupported_claim_restrictions
    ]
    for claim_restriction in unsupported_claim_restrictions:
        for evidence_restriction in relevant_evidence:
            if not set(evidence_restriction["terms"]) & set(claim_restriction["terms"]):
                continue
            relation_mismatch = relation_mismatch or (
                evidence_restriction["relation"] != claim_restriction["relation"]
            )
            negated_condition = negated_condition or bool(claim_restriction.get("negated")) != bool(
                evidence_restriction.get("negated")
            )
    mismatches = sorted(set([*missing_conditions, *unsupported_claim_conditions]))
    reported_mismatches = sorted(
        set(missing_conditions if missing_conditions else unsupported_claim_conditions)
    )
    reason_code = "CONDITIONS_MATCH"
    if mismatches:
        if negated_condition:
            reason_code = "CONDITION_NEGATED"
        elif relation_mismatch:
            reason_code = "CONDITION_RELATION_MISMATCH"
        else:
            reason_code = "CONDITION_OMITTED_OR_CHANGED"
    return {
        "kind": "condition",
        "status": "inconsistent" if mismatches else "consistent",
        "reason_code": reason_code,
        "claim_values": sorted({restriction["condition"] for restriction in claim_restrictions}),
        "missing_values": reported_mismatches,
    }


def _evidence_texts(grounding: Mapping[str, Any], evidence_ids: list[str]) -> list[str]:
    return [str(item.get("text") or "") for item in _evidence_items(grounding, evidence_ids)]


def _evidence_items(
    grounding: Mapping[str, Any], evidence_ids: list[str]
) -> list[Mapping[str, Any]]:
    raw_evidence = grounding.get("evidence")
    if not isinstance(raw_evidence, list):
        return []
    allowed = set(evidence_ids)
    return [
        item
        for item in raw_evidence
        if isinstance(item, dict) and item.get("evidence_id") in allowed
    ]


def _all_evidence_items(grounding: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_evidence = grounding.get("evidence")
    if not isinstance(raw_evidence, list):
        return []
    return [item for item in raw_evidence if isinstance(item, dict)]


def _document_temporal_state(
    item: Mapping[str, Any], reference: date
) -> tuple[str, dict[str, str]]:
    metadata = extract_document_temporal_metadata(item)
    effective_raw = metadata.get("effective_at")
    superseded_raw = metadata.get("superseded_at")
    if effective_raw is None:
        return "validity_missing", metadata
    effective = date.fromisoformat(effective_raw)
    if effective > reference:
        return "future", metadata
    if superseded_raw is not None and date.fromisoformat(superseded_raw) <= reference:
        return "superseded", metadata
    return "active", metadata


def _check_freshness(
    evidence_items: list[Mapping[str, Any]],
    all_evidence_items: list[Mapping[str, Any]],
    temporal_context: Mapping[str, Any] | None,
    freshness_intent: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    intent = sanitize_freshness_intent(freshness_intent)
    if not intent or not intent["required"]:
        return None
    mode = str(intent["mode"])
    if mode == "recent":
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_RECENCY_WINDOW_UNDEFINED",
            "claim_values": [mode],
            "missing_values": ["recency_window"],
        }
    reference = _temporal_reference_date(temporal_context)
    if reference is None:
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_REFERENCE_MISSING",
            "claim_values": [mode],
            "missing_values": ["request_clock"],
        }
    cited_knowledge = [item for item in evidence_items if item.get("source_type") != "web"]
    if len(cited_knowledge) != len(evidence_items) or not cited_knowledge:
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_VALIDITY_METADATA_MISSING",
            "claim_values": [mode, reference.isoformat()],
            "missing_values": ["knowledge_base_temporal_metadata"],
        }

    if mode in {"current", "latest_effective"}:
        cited_states = [_document_temporal_state(item, reference) for item in cited_knowledge]
        missing = [
            str(item.get("evidence_id") or "effective_at")
            for item, (state, _) in zip(cited_knowledge, cited_states, strict=True)
            if state == "validity_missing"
        ]
        if missing:
            return {
                "kind": "freshness",
                "status": "unknown",
                "reason_code": "FRESHNESS_VALIDITY_METADATA_MISSING",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": missing,
            }
        if any(state == "future" for state, _ in cited_states):
            return {
                "kind": "freshness",
                "status": "inconsistent",
                "reason_code": "FRESHNESS_EVIDENCE_NOT_YET_EFFECTIVE",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": ["effective_at<=reference_date"],
            }
        if any(state == "superseded" for state, _ in cited_states):
            return {
                "kind": "freshness",
                "status": "inconsistent",
                "reason_code": "FRESHNESS_EVIDENCE_SUPERSEDED",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": ["reference_date<superseded_at"],
            }
        if mode == "current":
            return {
                "kind": "freshness",
                "status": "consistent",
                "reason_code": "FRESHNESS_CURRENT_VERSION_CONFIRMED",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": [],
            }

        cited_families = [
            extract_document_version_metadata(item).get("version_family")
            for item in cited_knowledge
        ]
        if any(family is None for family in cited_families):
            return {
                "kind": "freshness",
                "status": "unknown",
                "reason_code": "FRESHNESS_VERSION_FAMILY_MISSING",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": ["version_family"],
            }
        families = {str(family) for family in cited_families}
        if len(families) != 1:
            return {
                "kind": "freshness",
                "status": "unknown",
                "reason_code": "FRESHNESS_VERSION_FAMILY_AMBIGUOUS",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": ["single_version_family"],
            }
        version_family = next(iter(families))
        comparison_items = [
            item
            for item in all_evidence_items
            if item.get("source_type") != "web"
            and extract_document_version_metadata(item).get("version_family") == version_family
        ]
        comparison_states = [_document_temporal_state(item, reference) for item in comparison_items]
        missing_comparison = [
            str(item.get("evidence_id") or "effective_at")
            for item, (state, _) in zip(comparison_items, comparison_states, strict=True)
            if state == "validity_missing"
        ]
        if missing_comparison:
            return {
                "kind": "freshness",
                "status": "unknown",
                "reason_code": "FRESHNESS_ORDERING_METADATA_MISSING",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": missing_comparison,
            }
        active_dates = [
            date.fromisoformat(metadata["effective_at"])
            for state, metadata in comparison_states
            if state == "active"
        ]
        if not active_dates:
            return {
                "kind": "freshness",
                "status": "inconsistent",
                "reason_code": "FRESHNESS_NO_CURRENT_VERSION",
                "claim_values": [mode, reference.isoformat()],
                "missing_values": ["active_version"],
            }
        newest = max(active_dates)
        cited_dates = {
            date.fromisoformat(metadata["effective_at"])
            for state, metadata in cited_states
            if state == "active"
        }
        matched = newest in cited_dates
        return {
            "kind": "freshness",
            "status": "consistent" if matched else "inconsistent",
            "reason_code": (
                "FRESHNESS_LATEST_EFFECTIVE_CONFIRMED"
                if matched
                else "FRESHNESS_NEWER_EVIDENCE_AVAILABLE"
            ),
            "claim_values": [mode, newest.isoformat()],
            "missing_values": [] if matched else [newest.isoformat()],
        }

    cited_families = [
        extract_document_version_metadata(item).get("version_family") for item in cited_knowledge
    ]
    if any(family is None for family in cited_families):
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_VERSION_FAMILY_MISSING",
            "claim_values": [mode, reference.isoformat()],
            "missing_values": ["version_family"],
        }
    families = {str(family) for family in cited_families}
    if len(families) != 1:
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_VERSION_FAMILY_AMBIGUOUS",
            "claim_values": [mode, reference.isoformat()],
            "missing_values": ["single_version_family"],
        }
    version_family = next(iter(families))
    comparison_items = [
        item
        for item in all_evidence_items
        if item.get("source_type") != "web"
        and extract_document_version_metadata(item).get("version_family") == version_family
    ]
    comparison_metadata = [extract_document_temporal_metadata(item) for item in comparison_items]
    missing_publication = [
        str(item.get("evidence_id") or "published_at")
        for item, metadata in zip(comparison_items, comparison_metadata, strict=True)
        if "published_at" not in metadata
    ]
    if missing_publication:
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_ORDERING_METADATA_MISSING",
            "claim_values": [mode, reference.isoformat()],
            "missing_values": missing_publication,
        }
    eligible_dates = [
        date.fromisoformat(metadata["published_at"])
        for metadata in comparison_metadata
        if date.fromisoformat(metadata["published_at"]) <= reference
        and (
            "superseded_at" not in metadata
            or date.fromisoformat(metadata["superseded_at"]) > reference
        )
    ]
    if not eligible_dates:
        return {
            "kind": "freshness",
            "status": "inconsistent",
            "reason_code": "FRESHNESS_NO_ELIGIBLE_PUBLICATION",
            "claim_values": [mode, reference.isoformat()],
            "missing_values": ["eligible_publication"],
        }
    newest = max(eligible_dates)
    cited_metadata = [extract_document_temporal_metadata(item) for item in cited_knowledge]
    if any("published_at" not in metadata for metadata in cited_metadata):
        return {
            "kind": "freshness",
            "status": "unknown",
            "reason_code": "FRESHNESS_ORDERING_METADATA_MISSING",
            "claim_values": [mode, newest.isoformat()],
            "missing_values": ["published_at"],
        }
    if any(date.fromisoformat(metadata["published_at"]) > reference for metadata in cited_metadata):
        return {
            "kind": "freshness",
            "status": "inconsistent",
            "reason_code": "FRESHNESS_PUBLICATION_IN_FUTURE",
            "claim_values": [mode, newest.isoformat()],
            "missing_values": ["published_at<=reference_date"],
        }
    if any(
        "superseded_at" in metadata and date.fromisoformat(metadata["superseded_at"]) <= reference
        for metadata in cited_metadata
    ):
        return {
            "kind": "freshness",
            "status": "inconsistent",
            "reason_code": "FRESHNESS_EVIDENCE_SUPERSEDED",
            "claim_values": [mode, newest.isoformat()],
            "missing_values": ["reference_date<superseded_at"],
        }
    cited_dates = {date.fromisoformat(metadata["published_at"]) for metadata in cited_metadata}
    matched = newest in cited_dates
    return {
        "kind": "freshness",
        "status": "consistent" if matched else "inconsistent",
        "reason_code": (
            "FRESHNESS_LATEST_PUBLICATION_CONFIRMED"
            if matched
            else "FRESHNESS_NEWER_EVIDENCE_AVAILABLE"
        ),
        "claim_values": [mode, newest.isoformat()],
        "missing_values": [] if matched else [newest.isoformat()],
    }


def _negation_signature(text: str) -> tuple[str, bool]:
    raw = str(text or "")
    has_negation = NEGATION_PATTERN.search(raw) is not None
    without_negation = NEGATION_PATTERN.sub("", raw)
    core = PUNCTUATION_OR_SPACE.sub("", without_negation).lower()
    return core, has_negation


def _check_negation(claim_text: str, evidence_texts: list[str]) -> dict[str, Any] | None:
    claim_core, claim_negative = _negation_signature(claim_text)
    if len(claim_core) < 6:
        return None
    candidates: list[tuple[str, bool]] = []
    for evidence_text in evidence_texts:
        for sentence in CLAUSE_SPLIT.split(evidence_text):
            evidence_core, evidence_negative = _negation_signature(sentence)
            if len(evidence_core) < 6:
                continue
            if claim_core in evidence_core or evidence_core in claim_core:
                candidates.append((sentence.strip(), evidence_negative))
    if not candidates:
        return None
    if not claim_negative and not any(evidence_negative for _, evidence_negative in candidates):
        return None
    if any(evidence_negative == claim_negative for _, evidence_negative in candidates):
        return {
            "kind": "negation",
            "status": "consistent",
            "reason_code": "NEGATION_POLARITY_MATCH",
        }
    return {
        "kind": "negation",
        "status": "inconsistent",
        "reason_code": "NEGATION_POLARITY_CONFLICT",
    }


def _check_relative_time(
    claim_text: str,
    evidence_items: list[Mapping[str, Any]],
    temporal_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    terms = _extract_relative_time_terms(claim_text)
    if not terms:
        return None
    reference = _temporal_reference_date(temporal_context)
    if reference is None:
        return {
            "kind": "relative_time",
            "status": "unknown",
            "reason_code": "RELATIVE_TIME_REFERENCE_MISSING",
            "claim_values": list(terms),
            "missing_values": list(terms),
        }
    claim_values = _resolve_relative_time_terms(terms, reference)
    evidence_values: set[str] = set()
    evidence_relative_terms: set[str] = set()
    for evidence_item in evidence_items:
        item_values, unanchored_terms = _evidence_temporal_values(evidence_item)
        evidence_values.update(item_values)
        evidence_relative_terms.update(unanchored_terms)
    missing_values = sorted(claim_values - evidence_values)
    if missing_values and evidence_relative_terms:
        return {
            "kind": "relative_time",
            "status": "unknown",
            "reason_code": "RELATIVE_TIME_EVIDENCE_ANCHOR_MISSING",
            "claim_values": sorted(claim_values),
            "missing_values": sorted(evidence_relative_terms),
        }
    entity_mismatches = (
        _relative_time_entity_mismatches(claim_text, evidence_items, claim_values)
        if not missing_values
        else []
    )
    failed_values = missing_values or entity_mismatches
    return {
        "kind": "relative_time",
        "status": "inconsistent" if failed_values else "consistent",
        "reason_code": (
            "RELATIVE_TIME_NOT_IN_EVIDENCE"
            if missing_values
            else "RELATIVE_TIME_ENTITY_MISMATCH"
            if entity_mismatches
            else "RELATIVE_TIME_VALUES_MATCH"
        ),
        "claim_values": sorted(claim_values),
        "missing_values": failed_values,
    }


def check_claim_consistency(
    claim: Mapping[str, Any],
    grounding: Mapping[str, Any],
    *,
    temporal_context: Mapping[str, Any] | None = None,
    freshness_intent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run conservative deterministic checks against the claim's cited evidence."""

    structural_status = str(claim.get("status") or "unsupported")
    evidence_ids = [item for item in claim.get("evidence_ids", []) if isinstance(item, str)]
    if structural_status not in {"supported", "conflicting"} or not evidence_ids:
        return {
            "status": "not_checked",
            "checks": [],
            "reason_codes": ["VALID_CITATION_REQUIRED"],
        }
    evidence_items = _evidence_items(grounding, evidence_ids)
    texts = [str(item.get("text") or "") for item in evidence_items]
    if not texts:
        return {
            "status": "not_checked",
            "checks": [],
            "reason_codes": ["CITED_EVIDENCE_TEXT_MISSING"],
        }

    claim_text = str(claim.get("text") or "")
    combined_evidence = "\n".join(texts)
    checks: list[dict[str, Any]] = []

    freshness_check = _check_freshness(
        evidence_items,
        _all_evidence_items(grounding),
        temporal_context,
        freshness_intent,
    )
    if freshness_check is not None:
        checks.append(freshness_check)

    relative_time_check = _check_relative_time(
        claim_text,
        evidence_items,
        temporal_context,
    )
    if relative_time_check is not None:
        checks.append(relative_time_check)

    claim_dates, _ = _extract_dates(claim_text)
    if claim_dates:
        evidence_dates, _ = _extract_dates(combined_evidence)
        missing_dates = sorted(claim_dates - evidence_dates)
        checks.append(
            {
                "kind": "date",
                "status": "inconsistent" if missing_dates else "consistent",
                "reason_code": ("DATE_NOT_IN_EVIDENCE" if missing_dates else "DATE_VALUES_MATCH"),
                "claim_values": sorted(claim_dates),
                "missing_values": missing_dates,
            }
        )

    claim_versions, _ = _extract_versions(claim_text)
    if claim_versions:
        evidence_versions, _ = _extract_versions(combined_evidence)
        missing_versions = sorted(claim_versions - evidence_versions)
        checks.append(
            {
                "kind": "version",
                "status": "inconsistent" if missing_versions else "consistent",
                "reason_code": (
                    "VERSION_NOT_IN_EVIDENCE" if missing_versions else "VERSION_VALUES_MATCH"
                ),
                "claim_values": sorted(claim_versions),
                "missing_values": missing_versions,
            }
        )

    claim_ranges = _extract_ranges(claim_text)
    if claim_ranges:
        evidence_ranges = _extract_ranges(combined_evidence)
        missing_ranges = sorted(claim_ranges - evidence_ranges)
        entity_mismatched_ranges = _fact_entity_mismatches(
            claim_text,
            texts,
            _extract_ranges,
        )
        range_mismatches = missing_ranges or entity_mismatched_ranges
        checks.append(
            {
                "kind": "range",
                "status": "inconsistent" if range_mismatches else "consistent",
                "reason_code": (
                    "RANGE_NOT_IN_EVIDENCE"
                    if missing_ranges
                    else "RANGE_ENTITY_MISMATCH"
                    if entity_mismatched_ranges
                    else "RANGE_VALUES_MATCH"
                ),
                "claim_values": sorted(claim_ranges),
                "missing_values": range_mismatches,
            }
        )

    claim_quantities = _extract_quantities(claim_text)
    if claim_quantities:
        evidence_quantities = _extract_quantities(combined_evidence)
        missing_quantities = sorted(claim_quantities - evidence_quantities)
        entity_mismatched_quantities = _fact_entity_mismatches(
            claim_text,
            texts,
            _extract_quantities,
        )
        quantity_mismatches = missing_quantities or entity_mismatched_quantities
        checks.append(
            {
                "kind": "quantity",
                "status": "inconsistent" if quantity_mismatches else "consistent",
                "reason_code": (
                    "QUANTITY_NOT_IN_EVIDENCE"
                    if missing_quantities
                    else "QUANTITY_ENTITY_MISMATCH"
                    if entity_mismatched_quantities
                    else "QUANTITY_VALUES_MATCH"
                ),
                "claim_values": sorted(claim_quantities),
                "missing_values": quantity_mismatches,
            }
        )

    negation_check = _check_negation(claim_text, texts)
    if negation_check is not None:
        checks.append(negation_check)

    condition_check = _check_conditions(claim_text, texts)
    if condition_check is not None:
        checks.append(condition_check)

    if any(check["status"] == "inconsistent" for check in checks):
        status = "inconsistent"
    elif any(check["status"] == "unknown" for check in checks):
        status = "unknown"
    elif checks:
        status = "consistent"
    else:
        status = "not_applicable"
    return {
        "status": status,
        "checks": checks,
        "reason_codes": list(dict.fromkeys(str(check["reason_code"]) for check in checks)),
    }


def assess_grounding_consistency(
    grounding: Mapping[str, Any],
    *,
    temporal_context: Mapping[str, Any] | None = None,
    freshness_intent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a policy-ready grounding copy enriched with consistency results."""

    assessed = deepcopy(dict(grounding))
    raw_claims = assessed.get("claims")
    claims = raw_claims if isinstance(raw_claims, list) else []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        structural_status = str(claim.get("status") or "unsupported")
        consistency = check_claim_consistency(
            claim,
            assessed,
            temporal_context=temporal_context,
            freshness_intent=freshness_intent,
        )
        claim["structural_status"] = structural_status
        claim["consistency_status"] = consistency["status"]
        claim["consistency_checks"] = consistency["checks"]
        claim["consistency_reason_codes"] = consistency["reason_codes"]
        effective_status = (
            "unsupported"
            if consistency["status"] in {"inconsistent", "unknown"}
            else structural_status
        )
        claim["effective_status"] = effective_status
    assessed["structural_state"] = grounding.get("state")
    _recompute_grounding_state(assessed)
    return assessed


def _recompute_grounding_state(grounding: dict[str, Any]) -> None:
    claims = _claims(grounding)
    supported_count = sum(
        str(claim.get("effective_status") or claim.get("status") or "unsupported")
        in {"supported", "conflicting"}
        for claim in claims
    )
    has_conflict = any(
        str(claim.get("effective_status") or claim.get("status")) == "conflicting"
        for claim in claims
    )
    if has_conflict:
        state = "conflicting_evidence"
    elif claims and supported_count == len(claims):
        state = "fully_grounded"
    elif supported_count:
        state = "partially_grounded"
    else:
        state = "insufficient_evidence"
    grounding["state"] = state
    grounding["claim_count"] = len(claims)
    grounding["supported_claim_count"] = supported_count
    grounding["unsupported_claim_count"] = len(claims) - supported_count


def _evidence_pairs(
    grounding: Mapping[str, Any], evidence_ids: list[str]
) -> tuple[tuple[str, str], ...]:
    raw_evidence = grounding.get("evidence")
    if not isinstance(raw_evidence, list):
        return ()
    allowed = set(evidence_ids)
    return tuple(
        (str(item.get("evidence_id")), str(item.get("text") or ""))
        for item in raw_evidence
        if isinstance(item, dict)
        and item.get("evidence_id") in allowed
        and str(item.get("text") or "")
    )


def _has_exact_citation_span(claim: Mapping[str, Any], grounding: Mapping[str, Any]) -> bool:
    spans = claim.get("citation_spans")
    raw_evidence = grounding.get("evidence")
    if not isinstance(spans, list) or not isinstance(raw_evidence, list):
        return False
    evidence_by_id = {
        str(item.get("evidence_id")): str(item.get("text") or "")
        for item in raw_evidence
        if isinstance(item, Mapping)
    }
    claim_core = _normalize_clause(str(claim.get("text") or ""))
    for span in spans:
        if not isinstance(span, Mapping) or span.get("match_type") != "normalized_exact":
            continue
        start = span.get("start")
        end = span.get("end")
        evidence_text = evidence_by_id.get(str(span.get("evidence_id") or ""), "")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(evidence_text)
        ):
            continue
        left = max(
            [evidence_text.rfind(boundary, 0, start) for boundary in "\n。！？!?；;，,"],
            default=-1,
        )
        right_candidates = [
            position
            for boundary in "\n。！？!?；;，,"
            if (position := evidence_text.find(boundary, end)) >= 0
        ]
        right = min(right_candidates) if right_candidates else len(evidence_text)
        sentence_core = _normalize_clause(evidence_text[left + 1 : right])
        if claim_core and sentence_core == claim_core:
            return True
    return False


def _set_entailment(
    claim: dict[str, Any],
    *,
    status: str,
    method: str,
    checker: str | None,
    checker_version: str | None,
    confidence: float | None,
    reason_codes: list[str],
) -> None:
    claim["entailment_status"] = status
    claim["entailment_method"] = method
    claim["entailment_checker"] = checker
    claim["entailment_checker_version"] = checker_version
    claim["entailment_confidence"] = confidence
    claim["entailment_reason_codes"] = reason_codes
    if status == "contradicted":
        claim["effective_status"] = "unsupported"


def assess_grounding_entailment(
    grounding: Mapping[str, Any],
    checker: BaseEntailmentChecker | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply exact-match and optional model NLI after deterministic checks."""

    assessed = deepcopy(dict(grounding))
    pending: list[EntailmentInput] = []
    exact_match_count = 0
    for claim in _claims(assessed):
        index = int(claim.get("index") or 0)
        structural_status = str(claim.get("structural_status") or claim.get("status") or "")
        effective_status = str(claim.get("effective_status") or structural_status)
        if effective_status == "unsupported" or structural_status not in {
            "supported",
            "conflicting",
        }:
            _set_entailment(
                claim,
                status="not_checked",
                method="not_checked",
                checker=None,
                checker_version=None,
                confidence=None,
                reason_codes=["ENTAILMENT_VALID_SUPPORTED_CITATION_REQUIRED"],
            )
            continue
        if structural_status == "conflicting":
            _set_entailment(
                claim,
                status="not_checked",
                method="not_checked",
                checker=None,
                checker_version=None,
                confidence=None,
                reason_codes=["ENTAILMENT_CONFLICT_REQUIRES_DISCLOSURE"],
            )
            continue
        if _has_exact_citation_span(claim, assessed):
            exact_match_count += 1
            _set_entailment(
                claim,
                status="entailed",
                method="exact_match",
                checker="deterministic_exact_match",
                checker_version="1.0.0",
                confidence=1.0,
                reason_codes=["ENTAILMENT_EXACT_MATCH"],
            )
            continue
        evidence_ids = [item for item in claim.get("evidence_ids", []) if isinstance(item, str)]
        evidence = _evidence_pairs(assessed, evidence_ids)
        if not evidence:
            _set_entailment(
                claim,
                status="not_checked",
                method="not_checked",
                checker=None,
                checker_version=None,
                confidence=None,
                reason_codes=["ENTAILMENT_EVIDENCE_TEXT_MISSING"],
            )
            continue
        if checker is None:
            _set_entailment(
                claim,
                status="not_checked",
                method="not_checked",
                checker=None,
                checker_version=None,
                confidence=None,
                reason_codes=["ENTAILMENT_CHECKER_DISABLED"],
            )
            continue
        pending.append(
            EntailmentInput(
                claim_index=index,
                claim_text=str(claim.get("text") or ""),
                evidence=evidence,
            )
        )

    checker_result = None
    findings: dict[int, Any] = {}
    if checker is not None and pending:
        try:
            candidate = checker.check(pending)
            requested_indices = {item.claim_index for item in pending}
            for finding in candidate.findings:
                finding_index = getattr(finding, "claim_index", None)
                if (
                    isinstance(finding_index, bool)
                    or not isinstance(finding_index, int)
                    or finding_index not in requested_indices
                    or finding_index in findings
                ):
                    raise ValueError("invalid entailment checker claim index")
                findings[finding_index] = finding
            checker_result = candidate
        except Exception:
            checker_result = None
            findings = {}
    checker_name = (
        str(checker_result.checker)
        if checker_result is not None
        else str(getattr(checker, "checker_name", "unknown"))
        if checker is not None
        else None
    )
    checker_version = (
        str(checker_result.checker_version)
        if checker_result is not None
        else str(getattr(checker, "checker_version", "unknown"))
        if checker is not None
        else None
    )
    for item in pending:
        claim = next(
            claim for claim in _claims(assessed) if int(claim.get("index") or 0) == item.claim_index
        )
        finding = findings.get(item.claim_index)
        status = str(getattr(finding, "status", "unknown"))
        if status not in {"entailed", "contradicted", "unknown"}:
            status = "unknown"
        confidence = getattr(finding, "confidence", None)
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            confidence = None
        if status in {"entailed", "contradicted"} and confidence is None:
            status = "unknown"
            finding = None
        reason_codes = [
            str(code) for code in getattr(finding, "reason_codes", ()) if isinstance(code, str)
        ] or [
            "ENTAILMENT_CHECKER_FAILED" if checker_result is None else "ENTAILMENT_RESULT_MISSING"
        ]
        _set_entailment(
            claim,
            status=status,
            method="semantic_nli",
            checker=checker_name,
            checker_version=checker_version,
            confidence=float(confidence) if confidence is not None else None,
            reason_codes=reason_codes,
        )

    _recompute_grounding_state(assessed)
    claims = _claims(assessed)
    metadata = {
        "version": 1,
        "checker": checker_name,
        "checker_version": checker_version,
        "status": (
            str(checker_result.status)
            if checker_result is not None
            else "failed"
            if pending and checker is not None
            else "disabled"
            if checker is None
            else "skipped"
        ),
        "token_usage": (
            max(int(checker_result.token_usage or 0), 0) if checker_result is not None else 0
        ),
        "eligible_claim_count": exact_match_count + len(pending),
        "exact_match_count": exact_match_count,
        "checker_claim_count": len(pending),
        "entailed_count": sum(claim.get("entailment_status") == "entailed" for claim in claims),
        "contradicted_count": sum(
            claim.get("entailment_status") == "contradicted" for claim in claims
        ),
        "unknown_count": sum(claim.get("entailment_status") == "unknown" for claim in claims),
        "not_checked_count": sum(
            claim.get("entailment_status") == "not_checked" for claim in claims
        ),
        "error_code": (
            str(checker_result.error_code)
            if checker_result is not None and checker_result.error_code
            else "ENTAILMENT_CHECKER_FAILED"
            if pending and checker is not None and checker_result is None
            else None
        ),
    }
    return assessed, metadata


def propagate_entailment_findings(
    grounding: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy one already-paid entailment result onto retained output claims."""

    propagated = deepcopy(dict(grounding))
    by_key = {
        (
            str(claim.get("text") or ""),
            tuple(item for item in claim.get("evidence_ids", []) if isinstance(item, str)),
        ): claim
        for claim in _claims(source)
    }
    fields = (
        "entailment_status",
        "entailment_method",
        "entailment_checker",
        "entailment_checker_version",
        "entailment_confidence",
        "entailment_reason_codes",
    )
    for claim in _claims(propagated):
        key = (
            str(claim.get("text") or ""),
            tuple(item for item in claim.get("evidence_ids", []) if isinstance(item, str)),
        )
        source_claim = by_key.get(key)
        if source_claim is None:
            continue
        for field in fields:
            claim[field] = deepcopy(source_claim.get(field))
        if claim.get("entailment_status") == "contradicted":
            claim["effective_status"] = "unsupported"
    _recompute_grounding_state(propagated)
    return propagated


def _distinct_evidence_sources(grounding: Mapping[str, Any], evidence_ids: list[str]) -> set[str]:
    raw_evidence = grounding.get("evidence")
    if not isinstance(raw_evidence, list):
        return set()
    allowed = set(evidence_ids)
    sources: set[str] = set()
    for item in raw_evidence:
        if not isinstance(item, Mapping) or item.get("evidence_id") not in allowed:
            continue
        source = next(
            (
                f"{field}:{value}"
                for field in (
                    "source_url",
                    "document_id",
                    "reference",
                    "display_name",
                    "location_id",
                    "evidence_id",
                )
                if (value := str(item.get(field) or ""))
            ),
            None,
        )
        if source is not None:
            sources.add(source)
    return sources


def assess_grounding_risk(
    grounding: Mapping[str, Any],
    risk_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply server-owned risk requirements to already verified claims."""

    assessed = deepcopy(dict(grounding))
    profile = dict(risk_profile or default_risk_profile())
    requirements = profile.get("requirements")
    requirements = requirements if isinstance(requirements, Mapping) else {}
    risk_level = str(profile.get("risk_level") or DEFAULT_RISK_LEVEL)
    require_entailment = bool(requirements.get("require_decisive_entailment", False))
    allow_unknown = bool(requirements.get("allow_unknown_entailment", True))
    minimum_evidence = max(int(requirements.get("minimum_evidence_count") or 1), 1)
    minimum_sources = max(int(requirements.get("minimum_distinct_source_count") or 1), 1)
    for claim in _claims(assessed):
        effective_status = str(
            claim.get("effective_status") or claim.get("status") or "unsupported"
        )
        if effective_status == "conflicting":
            claim["risk_status"] = "conflict_disclosed"
            claim["risk_checks"] = []
            claim["risk_reason_codes"] = ["RISK_CONFLICT_DISCLOSED"]
            continue
        if effective_status != "supported":
            claim["risk_status"] = "not_applicable"
            claim["risk_checks"] = []
            claim["risk_reason_codes"] = ["RISK_CLAIM_ALREADY_REJECTED"]
            continue
        evidence_ids = list(
            dict.fromkeys(item for item in claim.get("evidence_ids", []) if isinstance(item, str))
        )
        source_count = len(_distinct_evidence_sources(assessed, evidence_ids))
        entailment_status = str(claim.get("entailment_status") or "not_checked")
        checks: list[dict[str, Any]] = []
        if risk_level == "high" and require_entailment:
            decisive = entailment_status in {"entailed", "contradicted"}
            checks.append(
                {
                    "kind": "entailment",
                    "status": "passed" if decisive else "failed",
                    "reason_code": (
                        "HIGH_RISK_ENTAILMENT_DECISIVE"
                        if decisive
                        else "HIGH_RISK_ENTAILMENT_REQUIRED"
                    ),
                    "actual": entailment_status,
                    "required": "entailed_or_contradicted",
                }
            )
            if entailment_status == "unknown" and allow_unknown:
                checks[-1]["status"] = "passed"
        checks.extend(
            [
                {
                    "kind": "evidence_count",
                    "status": "passed" if len(evidence_ids) >= minimum_evidence else "failed",
                    "reason_code": (
                        "RISK_EVIDENCE_COUNT_MET"
                        if len(evidence_ids) >= minimum_evidence
                        else "HIGH_RISK_MINIMUM_EVIDENCE_NOT_MET"
                    ),
                    "actual": len(evidence_ids),
                    "required": minimum_evidence,
                },
                {
                    "kind": "source_count",
                    "status": "passed" if source_count >= minimum_sources else "failed",
                    "reason_code": (
                        "RISK_DISTINCT_SOURCE_COUNT_MET"
                        if source_count >= minimum_sources
                        else "HIGH_RISK_MINIMUM_DISTINCT_SOURCES_NOT_MET"
                    ),
                    "actual": source_count,
                    "required": minimum_sources,
                },
            ]
        )
        failed = [check for check in checks if check["status"] == "failed"]
        claim["risk_status"] = "rejected" if failed else "passed"
        claim["risk_checks"] = checks
        claim["risk_reason_codes"] = list(
            dict.fromkeys(str(check["reason_code"]) for check in checks)
        )
        if failed:
            claim["effective_status"] = "unsupported"
    _recompute_grounding_state(assessed)
    assessed["risk_level"] = risk_level
    return assessed


def _policy_for_state(
    state: str,
    *,
    can_enforce: bool,
    risk_profile: Mapping[str, Any],
) -> dict[str, Any]:
    risk_level = str(risk_profile.get("risk_level") or DEFAULT_RISK_LEVEL)
    policy_profile = "strict_high_risk" if risk_level == "high" else DEFAULT_POLICY_PROFILE
    if not can_enforce:
        return {
            "version": ANSWER_POLICY_VERSION,
            "profile": policy_profile,
            "risk_level": risk_level,
            "action": "observe",
            "reason_codes": ["TRUST_EVIDENCE_SNAPSHOT_MISSING"],
            "applied": False,
            "answer_changed": False,
        }
    if state == "fully_grounded":
        action = "allow"
        reasons = ["ANSWER_FULLY_GROUNDED"]
    elif state == "partially_grounded":
        action = "downgrade"
        reasons = ["ANSWER_PARTIALLY_GROUNDED"]
    elif state == "conflicting_evidence":
        action = "disclose_conflict"
        reasons = ["EVIDENCE_CONFLICT_DISCLOSED"]
    else:
        action = "refuse"
        reasons = ["EVIDENCE_INSUFFICIENT"]
    return {
        "version": ANSWER_POLICY_VERSION,
        "profile": policy_profile,
        "risk_level": risk_level,
        "action": action,
        "reason_codes": reasons,
        "applied": False,
        "answer_changed": False,
    }


def decide_answer_policy(
    grounding: Mapping[str, Any],
    *,
    evidence_snapshot_available: bool,
    risk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded policy decision for a structural grounding result."""

    raw_state = str(grounding.get("state") or "insufficient_evidence")
    state = raw_state if raw_state in TRUST_STATES else "insufficient_evidence"
    profile = dict(risk_profile or default_risk_profile())
    policy = _policy_for_state(
        state,
        can_enforce=evidence_snapshot_available,
        risk_profile=profile,
    )
    if any(claim.get("risk_status") == "rejected" for claim in _claims(grounding)):
        policy["reason_codes"] = list(
            dict.fromkeys([*policy["reason_codes"], "HIGH_RISK_REQUIREMENTS_NOT_MET"])
        )
    return policy


def _render_retained_claim(claim: Mapping[str, Any]) -> str | None:
    status = str(claim.get("effective_status") or claim.get("status") or "unsupported")
    if status not in {"supported", "conflicting"}:
        return None
    text = re.sub(
        r"\s+([。！？!?.,，；;：:])",
        r"\1",
        str(claim.get("text") or "").strip(),
    )
    evidence_ids = [
        str(item)
        for item in claim.get("evidence_ids", [])
        if isinstance(item, str) and item.startswith("E")
    ]
    if not text or not evidence_ids:
        return None
    if status == "conflicting":
        marker = f"[CONFLICT:{','.join(evidence_ids)}]"
    else:
        marker = "".join(f"[{item}]" for item in evidence_ids)
    return f"{text}{marker}"


def apply_answer_policy(
    answer: str,
    grounding: Mapping[str, Any],
    *,
    evidence_snapshot_available: bool,
    enforce: bool,
    risk_profile: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Apply the current fail-closed answer policy.

    Enforcement requires the exact evidence snapshot shown to the generation
    model.  Legacy callers without that snapshot remain observable but are not
    silently rewritten.
    """

    original = str(answer or "")
    policy = decide_answer_policy(
        grounding,
        evidence_snapshot_available=evidence_snapshot_available,
        risk_profile=risk_profile,
    )
    if not enforce or policy["action"] in {"observe", "allow", "disclose_conflict"}:
        return original, policy

    if policy["action"] == "downgrade":
        retained = [
            rendered
            for claim in _claims(grounding)
            if (rendered := _render_retained_claim(claim)) is not None
        ]
        final_answer = "\n\n".join(retained) if retained else INSUFFICIENT_EVIDENCE_ANSWER
    else:
        final_answer = INSUFFICIENT_EVIDENCE_ANSWER

    policy = dict(policy)
    policy["applied"] = True
    policy["answer_changed"] = final_answer != original
    return final_answer, policy


def _trust_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    structural_status = str(claim.get("structural_status") or claim.get("status") or "unsupported")
    status = str(claim.get("effective_status") or structural_status)
    if structural_status == "supported":
        citation_status = "valid"
        reasons = ["CITATION_VALID"]
    elif structural_status == "conflicting":
        citation_status = "conflicting"
        reasons = ["EVIDENCE_CONFLICT"]
    elif structural_status == "invalid_citation":
        citation_status = "invalid"
        reasons = ["CITATION_INVALID"]
    else:
        citation_status = "missing"
        reasons = ["CITATION_MISSING"]
    evidence_ids = [item for item in claim.get("evidence_ids", []) if isinstance(item, str)]
    return {
        "index": int(claim.get("index") or 0),
        "text": str(claim.get("text") or ""),
        "support_status": status,
        "structural_support_status": structural_status,
        "citation_status": citation_status,
        "entailment_status": str(claim.get("entailment_status") or "not_checked"),
        "entailment_method": str(claim.get("entailment_method") or "not_checked"),
        "entailment_checker": claim.get("entailment_checker"),
        "entailment_checker_version": claim.get("entailment_checker_version"),
        "consistency_status": str(claim.get("consistency_status") or "not_checked"),
        "consistency_checks": list(claim.get("consistency_checks") or []),
        "confidence": claim.get("entailment_confidence"),
        "risk_status": str(claim.get("risk_status") or "not_assessed"),
        "risk_checks": list(claim.get("risk_checks") or []),
        "evidence_ids": evidence_ids,
        "citation_spans": [
            dict(item) for item in claim.get("citation_spans", []) if isinstance(item, Mapping)
        ],
        "invalid_evidence_ids": [
            item for item in claim.get("invalid_evidence_ids", []) if isinstance(item, str)
        ],
        "reason_codes": list(
            dict.fromkeys(
                [
                    *reasons,
                    *list(claim.get("consistency_reason_codes") or []),
                    *list(claim.get("entailment_reason_codes") or []),
                    *list(claim.get("risk_reason_codes") or []),
                ]
            )
        ),
    }


def build_trust_report(
    final_grounding: Mapping[str, Any],
    *,
    original_grounding: Mapping[str, Any],
    evidence_snapshot_available: bool,
    policy: Mapping[str, Any],
    entailment: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    temporal_context: Mapping[str, Any] | None = None,
    freshness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the public Trust Layer v1 contract."""

    final_state = str(final_grounding.get("state") or "insufficient_evidence")
    original_state = str(original_grounding.get("state") or "insufficient_evidence")
    trust_status = final_state if evidence_snapshot_available else "not_assessed"
    final_claims = _claims(final_grounding)
    original_claims = _claims(original_grounding)
    entailment_details = dict(entailment or {})
    checker_claim_count = int(entailment_details.get("checker_claim_count") or 0)
    unknown_count = int(entailment_details.get("unknown_count") or 0)
    if checker_claim_count:
        verification_level = (
            "semantic_entailment_partial" if unknown_count else "semantic_entailment"
        )
    elif int(entailment_details.get("exact_match_count") or 0):
        verification_level = "exact_match_and_deterministic_consistency"
    else:
        verification_level = "deterministic_consistency"
    limitations = [
        "OPEN_ENDED_SEMANTIC_CONSISTENCY_NOT_CHECKED",
        "SAFETY_NOT_EVALUATED",
    ]
    if checker_claim_count == 0:
        limitations.insert(0, "SEMANTIC_ENTAILMENT_NOT_CHECKED")
    elif unknown_count:
        limitations.insert(0, "SEMANTIC_ENTAILMENT_INCOMPLETE")
    if any(
        check.get("kind") == "relative_time" and check.get("status") == "unknown"
        for claim in original_claims
        for check in claim.get("consistency_checks", [])
        if isinstance(check, Mapping)
    ):
        limitations.insert(0, "RELATIVE_TIME_VERIFICATION_INCOMPLETE")
    if any(
        check.get("kind") == "freshness" and check.get("status") == "unknown"
        for claim in original_claims
        for check in claim.get("consistency_checks", [])
        if isinstance(check, Mapping)
    ):
        limitations.insert(0, "FRESHNESS_VERIFICATION_INCOMPLETE")
    report = {
        "version": TRUST_CONTRACT_VERSION,
        "trust_status": trust_status,
        "safety_status": "not_evaluated",
        "verification_level": verification_level,
        "evidence_snapshot_available": bool(evidence_snapshot_available),
        "entailment": entailment_details,
        "risk": dict(risk or default_risk_profile()),
        "freshness": dict(freshness or {}),
        "temporal_context": dict(temporal_context or {}),
        "input": {
            "trust_status": original_state,
            "claim_count": int(original_grounding.get("claim_count") or 0),
            "supported_claim_count": int(original_grounding.get("supported_claim_count") or 0),
            "claims": [_trust_claim(claim) for claim in original_claims],
        },
        "output": {
            "trust_status": final_state,
            "claim_count": int(final_grounding.get("claim_count") or 0),
            "supported_claim_count": int(final_grounding.get("supported_claim_count") or 0),
            "claims": [_trust_claim(claim) for claim in final_claims],
        },
        "claims": [_trust_claim(claim) for claim in final_claims],
        "policy": dict(policy),
        "limitations": limitations,
    }
    if provenance:
        report["provenance"] = dict(provenance)
    return report
