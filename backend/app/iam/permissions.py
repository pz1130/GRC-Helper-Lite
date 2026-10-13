from enum import StrEnum


class Role(StrEnum):
    """四个角色，权限自高向低（spec §8.1）。"""

    ADMIN = "admin"
    GRC_LEAD = "grc_lead"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class Permission(StrEnum):
    """按 spec §8.1 的四行表拆出的操作权限。"""

    READ = "read"                        # 只读浏览全部业务数据
    DOCUMENT_WRITE = "document:write"    # 上传/编辑制度文件
    CONTROL_WRITE = "control:write"      # 编辑控制点
    FRAMEWORK_WRITE = "framework:write"  # 导入/编辑框架
    ENVIRONMENT_WRITE = "environment:write"  # 技术栈台账
    EVIDENCE_WRITE = "evidence:write"    # 证据登记
    ANSWER_DRAFT = "answer:draft"        # 起草审计答复
    ANSWER_FINALIZE = "answer:finalize"  # 答复定稿
    REVIEW_DECIDE = "review:decide"      # 确认队列决策（D12 守门权）
    MATURITY_SCORE = "maturity:score"    # 成熟度评分
    RISK_WRITE = "risk:write"            # 风险登记册
    USER_MANAGE = "user:manage"          # 用户管理
    LLM_CONFIG_WRITE = "llm_config:write"  # AI provider 配置
    REDACTION_WRITE = "redaction:write"    # 脱敏规则
    AUDIT_LOG_READ = "audit_log:read"      # 查看操作审计日志


_CONTRIBUTOR: frozenset[Permission] = frozenset({
    Permission.READ,
    Permission.DOCUMENT_WRITE,
    Permission.ENVIRONMENT_WRITE,
    Permission.EVIDENCE_WRITE,
    Permission.ANSWER_DRAFT,
})

_GRC_LEAD: frozenset[Permission] = _CONTRIBUTOR | frozenset({
    Permission.CONTROL_WRITE,
    Permission.FRAMEWORK_WRITE,
    Permission.ANSWER_FINALIZE,
    Permission.REVIEW_DECIDE,
    Permission.MATURITY_SCORE,
    Permission.RISK_WRITE,
    Permission.AUDIT_LOG_READ,
})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.READ}),
    Role.CONTRIBUTOR: _CONTRIBUTOR,
    Role.GRC_LEAD: _GRC_LEAD,
    Role.ADMIN: frozenset(Permission),
}


def has_permission(role: Role, perm: Permission) -> bool:
    return perm in ROLE_PERMISSIONS[role]
