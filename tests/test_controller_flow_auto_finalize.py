from decimal import Decimal
from types import SimpleNamespace

from app.controller import ApplicationController
from app.flow.models import FlowDclLine, FlowReviewItem, ReviewStatus
from app.flow.processor import FlowRun
from app.phase2.models import ProjectOrder


def test_recognised_flow_requires_explicit_approval_on_each_fresh_preview():
    order = ProjectOrder(2, "50594733", "FCA", None, category="Flow")
    line = FlowDclLine(
        "50594733", "Customer", "dcl.pdf", 1, "2.1", "AE07263",
        "Portable PDM", Decimal("1"), "Each",
    )
    item = FlowReviewItem(
        line, "AE07263", "{SO}-AE07263-{NN}", ["50594733-AE07263-01"],
        Decimal("1"), ReviewStatus.RECOGNISED,
    )
    flow_run = FlowRun(order, [], [line], [item], [])
    controller = ApplicationController.__new__(ApplicationController)
    controller.require_session = lambda: object()
    controller.flow = SimpleNamespace(prepare=lambda so, order=None: flow_run)

    class Reviews:
        def save_run(self, run):
            run.run_id = 7

        def load_items(self, _run_id):
            return [(11, item)]

    controller.flow_reviews = Reviews()

    supplied_snapshot = object()

    result, online = controller.phase2_preview(
        order.so, order=order, snapshot_override=supplied_snapshot,
    )
    assert result is flow_run
    assert online is False
    assert item.status == ReviewStatus.RECOGNISED
    assert item.approved is False
