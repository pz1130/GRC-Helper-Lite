import pytest

from app.iam.permissions import Permission, Role, has_permission


def test_admin_has_every_permission():
    for perm in Permission:
        assert has_permission(Role.ADMIN, perm), f"admin 应当拥有 {perm}"


@pytest.mark.parametrize("role", [Role.GRC_LEAD, Role.ADMIN])
def test_review_decide_granted_to_lead_and_admin(role):
    assert has_permission(role, Permission.REVIEW_DECIDE)


@pytest.mark.parametrize("role", [Role.CONTRIBUTOR, Role.VIEWER])
def test_review_decide_denied_below_lead(role):
    """spec D12：Contributor 不能做最终确认。这条不许放宽。"""
    assert not has_permission(role, Permission.REVIEW_DECIDE)


def test_contributor_can_write_business_data():
    assert has_permission(Role.CONTRIBUTOR, Permission.DOCUMENT_WRITE)
    assert has_permission(Role.CONTRIBUTOR, Permission.EVIDENCE_WRITE)


def test_grc_lead_cannot_touch_llm_config():
    """spec §8.1：GRC Lead 碰不到 AI 配置与脱敏规则。"""
    assert not has_permission(Role.GRC_LEAD, Permission.LLM_CONFIG_WRITE)
    assert not has_permission(Role.GRC_LEAD, Permission.REDACTION_WRITE)


def test_viewer_is_read_only():
    granted = {p for p in Permission if has_permission(Role.VIEWER, p)}
    assert granted == {Permission.READ}
