"""Deterministic, server-owned query risk classification for Trust Layer policy."""

from __future__ import annotations

import re
from typing import Any

RISK_CONTRACT_VERSION = 1
RISK_CLASSIFIER_VERSION = "1.0.0"

DOMAIN_PATTERNS = (
    (
        "health_safety",
        "HEALTH_OR_SAFETY_DOMAIN",
        re.compile(r"医疗|健康|诊断|治疗|用药|药物|剂量|急救|消防|事故|危险操作"),
    ),
    (
        "financial_policy",
        "FINANCIAL_DOMAIN",
        re.compile(r"报销|费用|金额|付款|支付|薪资|工资|税务|税率|预算|赔偿|发票|额度"),
    ),
    (
        "legal_compliance",
        "LEGAL_OR_COMPLIANCE_DOMAIN",
        re.compile(r"法律|法规|监管|合规|合同|审计|制度|公司政策|责任认定"),
    ),
    (
        "access_control",
        "ACCESS_CONTROL_DOMAIN",
        re.compile(
            r"ACL|RBAC|权限|授权|访问控制|管理员|组织所有者|删除审计|审批|密钥|密码",
            re.IGNORECASE,
        ),
    ),
)
DECISION_PATTERN = re.compile(
    r"谁|是否|能否|可以|允许|应当|必须|禁止|不得|规定|要求|责任|"
    r"多少|额度|上限|下限|最高|最低|期限|几天|比例|百分比|何时|截至|怎么办"
)
QUANTITATIVE_DECISION_PATTERN = re.compile(
    r"多少|额度|上限|下限|最高|最低|期限|几天|比例|百分比|%|％|金额|税率|剂量|次数|何时|截至"
)
IMMEDIATE_SAFETY_PATTERN = re.compile(
    r"急救|推荐剂量|怎么用药|如何用药|是否用药|诊断结果|治疗方案|危险操作|事故处理"
)


def default_risk_profile() -> dict[str, Any]:
    return {
        "version": RISK_CONTRACT_VERSION,
        "classifier": "deterministic_query_risk",
        "classifier_version": RISK_CLASSIFIER_VERSION,
        "risk_level": "medium",
        "query_type": "general_knowledge",
        "risk_factors": [],
        "requirements": {
            "require_citation": True,
            "require_decisive_entailment": False,
            "minimum_evidence_count": 1,
            "minimum_distinct_source_count": 1,
            "allow_unknown_entailment": True,
        },
    }


def classify_query_risk(query: str) -> dict[str, Any]:
    """Classify bounded risk without retaining or exposing the original query."""

    text = str(query or "")[:4000]
    matched_domains = [
        (query_type, factor)
        for query_type, factor, pattern in DOMAIN_PATTERNS
        if pattern.search(text)
    ]
    query_type = matched_domains[0][0] if matched_domains else "general_knowledge"
    factors = [factor for _, factor in matched_domains]
    decision_request = DECISION_PATTERN.search(text) is not None
    immediate_safety = IMMEDIATE_SAFETY_PATTERN.search(text) is not None
    quantitative = QUANTITATIVE_DECISION_PATTERN.search(text) is not None
    if decision_request:
        factors.append("DECISION_REQUEST")
    if quantitative:
        factors.append("QUANTITATIVE_DECISION")
    if immediate_safety:
        factors.append("IMMEDIATE_SAFETY_IMPACT")
    risk_level = "high" if matched_domains and (decision_request or immediate_safety) else "medium"
    multiple_sources_required = risk_level == "high" and quantitative
    return {
        "version": RISK_CONTRACT_VERSION,
        "classifier": "deterministic_query_risk",
        "classifier_version": RISK_CLASSIFIER_VERSION,
        "risk_level": risk_level,
        "query_type": query_type,
        "risk_factors": list(dict.fromkeys(factors)),
        "requirements": {
            "require_citation": True,
            "require_decisive_entailment": risk_level == "high",
            "minimum_evidence_count": 2 if multiple_sources_required else 1,
            "minimum_distinct_source_count": 2 if multiple_sources_required else 1,
            "allow_unknown_entailment": risk_level != "high",
        },
    }
