"""纯校验测试：不挂共享数据库 fixture 也能跑。"""

import pytest
from pydantic import ValidationError

from app.controls.schemas import ControlUpdateIn
from app.errors import AppError
from app.review.materialize import ControlPayload
from app.review.schemas import BulkAcceptIn, DecideIn
from app.review.service import Decision, validate_decision


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"decision": "modify"},
        {"decision": "reject"},
        {"decision": "reject", "reason": "  "},
        {"decision": "accept", "payload": {}},
        {"decision": "accept", "reason": "ignored?"},
        {"decision": "accept", "unexpected": True},
    ],
)
def test_decision_shape_is_strict(data):
    with pytest.raises(ValidationError):
        DecideIn.model_validate(data)


@pytest.mark.parametrize("ids", [[], [True], [0], [-1], ["1"], [1, 1], list(range(1, 202))])
def test_bulk_ids_are_bounded_unique_positive_integers(ids):
    with pytest.raises(ValidationError):
        BulkAcceptIn(ids=ids)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"title": None},
        {"statement": None},
        {"title": " "},
        {"title": "x" * 501},
        {"owner_user_id": True},
        {"status": "active"},
    ],
)
def test_control_patch_rejects_invalid_fields(changes):
    with pytest.raises(ValidationError):
        ControlUpdateIn.model_validate(changes)


def test_nullable_patch_fields_can_be_explicitly_cleared():
    parsed = ControlUpdateIn(category=None, owner_user_id=None,
                             expected={"category": "Access", "owner_user_id": 3})
    assert parsed.model_dump(exclude_unset=True, exclude={"expected"}) == {
        "category": None,
        "owner_user_id": None,
    }


def test_normal_control_cannot_claim_matrix_origin():
    with pytest.raises(ValidationError):
        ControlPayload.model_validate(
            {
                "title": "Title",
                "statement": "Statement",
                "origin": "matrix",
                "citations": [],
            }
        )


@pytest.mark.parametrize(
    "decision,payload,reason",
    [
        (Decision.MODIFY, None, None),
        (Decision.REJECT, None, " "),
        (Decision.ACCEPT, {}, None),
        (Decision.ACCEPT, None, "reason"),
    ],
)
def test_direct_service_decision_validation(decision, payload, reason):
    with pytest.raises(AppError):
        validate_decision(decision, payload, reason)
