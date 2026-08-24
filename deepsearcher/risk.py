"""Deterministic, server-owned query risk classification for Trust Layer policy."""

from __future__ import annotations

import re
from typing import Any

RISK_CONTRACT_VERSION = 1
RISK_CLASSIFIER_VERSION = "1.1.0"

# These are public, stable factor identifiers.  Product can use the
# preflight block below to reject a request before it reaches an answer path;
# Core never resolves, reads, or echoes the requested secret/target.
CREDENTIAL_DISCLOSURE_REQUEST = "CREDENTIAL_DISCLOSURE_REQUEST"
PRODUCTION_OPERATION_REQUEST = "PRODUCTION_OPERATION_REQUEST"

DOMAIN_PATTERNS = (
    (
        "health_safety",
        "HEALTH_OR_SAFETY_DOMAIN",
        re.compile(
            r"医疗|健康|诊断|治疗|用药|药物|剂量|急救|消防|事故|危险操作|"
            r"胃疼|胃痛|头疼|头痛|发烧|发热|咳嗽|症状|不舒服|就医|看医生|病情"
        ),
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
CREDENTIAL_DISCLOSURE_PATTERN = re.compile(
    r"(?:给我|告诉我|提供|显示|输出|返回|贴出|发我|获取|查找|找出|读取|导出|透露|泄露|"
    r"查看|帮我拿到|请问).{0,24}(?:api[_ -]?key|access[_ -]?token|bearer|token|secret|"
    r"password|passwd|凭据|密钥|密码|口令|私钥|客户端密钥|show me|give me|tell me|"
    r"reveal|disclose|print|provide)",
    re.IGNORECASE,
)
CREDENTIAL_DISCLOSURE_PATTERN_REVERSED = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|token|secret|password|passwd|凭据|密钥|密码|"
    r"口令|私钥|客户端密钥).{0,24}(?:是什么|是多少|给我|告诉我|提供|显示|输出|返回|贴出|"
    r"发我|获取|查找|找出|读取|导出|透露|泄露|查看)",
    re.IGNORECASE,
)
CREDENTIAL_ENGLISH_PATTERN = re.compile(
    r"(?:show|give|tell|provide|reveal|disclose|print|send|share|fetch|retrieve).{0,24}"
    r"(?:api[_ -]?key|access[_ -]?token|bearer|token|secret|password|passwd|credential|"
    r"private[_ -]?key)",
    re.IGNORECASE,
)
PRODUCTION_OPERATION_PATTERN = re.compile(
    r"(?:生产环境|线上环境|正式环境|生产库|线上库|真实服务器|正式服务器|线上服务)"
    r".{0,32}(?:上线|发布|部署|重启|停止|执行命令|运行命令|迁移|回滚|切换|开放端口|关闭端口)|"
    r"(?:删除生产|清空生产|删除数据库|清库|改防火墙|修改防火墙|改ssh|修改ssh|"
    r"开放生产端口|关闭生产端口|重启生产服务|停止生产服务)|"
    r"(?:production|prod|live).{0,32}(?:deploy|release|publish|restart|stop|run|execute|"
    r"delete|drop|migrate|rollback|switch|firewall|ssh|port)",
    re.IGNORECASE,
)


def default_risk_profile() -> dict[str, Any]:
    return {
        "version": RISK_CONTRACT_VERSION,
        "classifier": "deterministic_query_risk",
        "classifier_version": RISK_CLASSIFIER_VERSION,
        "risk_level": "medium",
        "query_type": "general_knowledge",
        "risk_factors": [],
        "preflight": {
            "decision": "allow",
            "action": "continue",
            "code": None,
            "requires_product_rejection": False,
        },
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
    credential_request = bool(
        CREDENTIAL_DISCLOSURE_PATTERN.search(text)
        or CREDENTIAL_DISCLOSURE_PATTERN_REVERSED.search(text)
        or CREDENTIAL_ENGLISH_PATTERN.search(text)
    )
    production_operation = bool(PRODUCTION_OPERATION_PATTERN.search(text))
    if credential_request:
        factors.append(CREDENTIAL_DISCLOSURE_REQUEST)
        if not matched_domains:
            query_type = "access_control"
            factors.append("ACCESS_CONTROL_DOMAIN")
    if production_operation:
        factors.append(PRODUCTION_OPERATION_REQUEST)

    # Credential disclosure and production-operation requests are always high
    # risk regardless of whether the surrounding wording contains a domain or
    # decision keyword.  This initial profile is passed into later trace and
    # query stages unchanged.
    risk_level = (
        "high"
        if credential_request
        or production_operation
        or (matched_domains and (decision_request or immediate_safety))
        else "medium"
    )
    multiple_sources_required = risk_level == "high" and quantitative
    preflight_code = None
    if credential_request:
        preflight_code = CREDENTIAL_DISCLOSURE_REQUEST
    elif production_operation:
        preflight_code = PRODUCTION_OPERATION_REQUEST
    preflight_reject = credential_request
    return {
        "version": RISK_CONTRACT_VERSION,
        "classifier": "deterministic_query_risk",
        "classifier_version": RISK_CLASSIFIER_VERSION,
        "risk_level": risk_level,
        "query_type": query_type,
        "risk_factors": list(dict.fromkeys(factors)),
        "preflight": {
            "decision": "reject" if preflight_reject else ("review" if preflight_code else "allow"),
            "action": "reject" if preflight_reject else ("require_review" if preflight_code else "continue"),
            "code": preflight_code,
            "requires_product_rejection": bool(preflight_reject),
        },
        "requirements": {
            "require_citation": True,
            "require_decisive_entailment": risk_level == "high",
            "minimum_evidence_count": 2 if multiple_sources_required else 1,
            "minimum_distinct_source_count": 2 if multiple_sources_required else 1,
            "allow_unknown_entailment": risk_level != "high",
        },
    }
