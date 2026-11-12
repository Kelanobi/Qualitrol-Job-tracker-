from decimal import Decimal

from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus
from app.flow.review_service import FlowReviewService


def item(serials, status=ReviewStatus.RECOGNISED, category="Window"):
    line = FlowDclLine("50565048", "C", "DCL.pdf", 1, None, "AE02909", "Window", Decimal(1), "Each")
    return FlowReviewItem(line, category, "{SO}-WIN-{NN}", serials, Decimal(1), status)


def test_bulk_approval_requires_valid_unique_serials():
    items = [(1, item(["50565048-WIN-01"])), (2, item(["50565048-WIN-01"]))]
    errors = FlowReviewService.approve(items, {1, 2})
    assert set(errors) == {1, 2}
    assert all(entry.status != ReviewStatus.APPROVED for _, entry in items)


def test_valid_selected_items_are_approved_but_not_unselected_items():
    items = [(1, item(["50565048-WIN-01"])), (2, item(["50565048-WIN-02"]))]
    assert FlowReviewService.approve(items, {1}) == {}
    assert items[0][1].approved is True
    assert items[1][1].approved is False


def test_exclusion_requires_reason():
    excluded = item([], ReviewStatus.EXCLUDED)
    errors = FlowReviewService.validate([(1, excluded)])
    assert errors[1] == ["Exclusion requires a reason."]


def test_metre_cable_edit_is_an_authoritative_user_override():
    line = FlowDclLine(
        "50605782", "Customer", "dcl.pdf", 1, None, "CFL00335", "Cable",
        Decimal("300"), "Each",
    )
    cable = FlowReviewItem(
        line, "UHF cable", "{SO}-CFL00335-{Q}m",
        ["50605782-CFL00335-400m"], Decimal("300"), ReviewStatus.RECOGNISED,
    )
    assert FlowReviewService.validate([(1, cable)]) == {}
