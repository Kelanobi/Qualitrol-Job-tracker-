from decimal import Decimal

from app.flow.models import FlowDclLine, ReviewStatus
from app.flow.serial_policy import FlowSerialPolicy


def line(code, quantity, unit="Each", description="Material", so="50565048"):
    return FlowDclLine(
        so=so, customer="Customer", source_file="DCL.pdf", page=1,
        line_reference="1", part_code=code, description=description,
        quantity=Decimal(str(quantity)), unit=unit,
    )


def test_window_material_quantity_12_produces_12_rows():
    item = FlowSerialPolicy().propose([line("AE02909", 12, description="Window Material (Metric)")])[0]
    assert item.proposed_serials == [f"50565048-WIN-{number:02d}" for number in range(1, 13)]


def test_ordinary_material_thresholds_are_flow_only():
    policy = FlowSerialPolicy()
    for quantity, expected in [(1, 1), (2, 2), (19, 19), (20, 1), (30, 1), (300, 1)]:
        item = policy.propose([line("AE01676-03", quantity, description="BC48 coupler")])[0]
        assert len(item.proposed_serials) == expected
        assert all("BC48" in serial and "BC24" not in serial for serial in item.proposed_serials)


def test_ocu_is_uncapped_and_supports_three_digit_suffix():
    item = FlowSerialPolicy().propose([
        line(None, 101, description="OPTICAL CONVERTER UNIT", so="50602018")
    ])[0]
    assert len(item.proposed_serials) == 101
    assert item.proposed_serials[-1] == "50602018-OCU-101"


def test_metre_cables_make_exactly_one_quantity_serial():
    items = FlowSerialPolicy().propose([
        line("CFL00335", 300, "m", "UHF cable", "50605782"),
        line("CFL00247", 600, "metres", "7 core cable", "50605782"),
    ])
    assert items[0].proposed_serials == ["50605782-CFL00335-300m"]
    assert items[1].proposed_serials == ["50605782-CFL00247-600m"]


def test_approved_specific_patterns_and_suffixes():
    items = FlowSerialPolicy().propose([
        line("AE02091", 1, description="Data Acquisition", so="50599846"),
        line("AE02092", 1, description="PDM Controller", so="50599846"),
        line("CEK00378", 1, description="Ethernet Switch", so="50599846"),
    ])
    assert [item.proposed_serials[0] for item in items] == [
        "50599846-OCU-PCB91-01", "50599846-OCU-PCB92-01", "50599846-OCU-Switch-01"
    ]


def test_same_prefix_increments_across_whole_so():
    items = FlowSerialPolicy().propose([
        line("AE07264", 1, description="Portable PDM"),
        line("AE07262", 2, description="Portable PDM"),
    ])
    assert [serial for item in items for serial in item.proposed_serials] == [
        "50565048-POR-01", "50565048-POR-02", "50565048-POR-03"
    ]


def test_unmapped_part_number_uses_approved_part_number_fallback():
    item = FlowSerialPolicy().propose([
        line(
            "AE07263", 2,
            description="Portable PDM System, 3 Channel Siemens c/w Accessories",
            so="50594733",
        )
    ])[0]

    assert item.status == ReviewStatus.NEEDS_REVIEW
    assert item.category == "AE07263"
    assert item.serial_pattern == "{SO}-AE07263-{NN}"
    assert item.proposed_serials == ["50594733-AE07263-01", "50594733-AE07263-02"]


def test_unmapped_flow_part_above_20_uses_full_quantity_once_and_requires_review():
    item = FlowSerialPolicy().propose([
        line("CEH01026", 105, description="N-TYPE male straight connector", so="50602018")
    ])[0]

    assert item.status == ReviewStatus.NEEDS_REVIEW
    assert item.serial_pattern == "{SO}-CEH01026-{Q}"
    assert item.proposed_serials == ["50602018-CEH01026-105"]


def test_unknown_and_invalid_quantities_require_review():
    unknown = FlowSerialPolicy().propose([line(None, 1)])[0]
    fractional = FlowSerialPolicy().propose([line("AE02909", "1.5")])[0]
    assert unknown.status == ReviewStatus.NEEDS_REVIEW and unknown.proposed_serials == []
    assert fractional.status == ReviewStatus.BLOCKED


def test_unmapped_part_number_is_sanitized_for_serial_use():
    item = FlowSerialPolicy().propose([
        line(" abc / 123 ", 1, description="Unmapped assembly", so="50600000")
    ])[0]

    assert item.proposed_serials == ["50600000-ABC-123-01"]
