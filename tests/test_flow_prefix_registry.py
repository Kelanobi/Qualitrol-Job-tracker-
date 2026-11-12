from decimal import Decimal

from openpyxl import Workbook, load_workbook

from app.flow.catalogue import MaterialCatalogue
from app.flow.models import FlowDclLine, ReviewStatus
from app.flow.prefix_registry import FlowPrefixRegistry
from app.flow.serial_policy import FlowSerialPolicy


def make_workbook(path):
    workbook = Workbook()
    workbook.active.title = "Sheet1"
    sheet = workbook.create_sheet("Sheet2")
    sheet.append(["Part Number", "Prefix"])
    workbook.save(path)


def line(part, quantity, description="Material"):
    return FlowDclLine(
        so="50602018", customer="Customer", source_file="DCL.pdf", page=1,
        line_reference="1", part_code=part, description=description,
        quantity=Decimal(str(quantity)), unit="Each",
    )


def test_shared_prefix_is_saved_once_and_visible_to_another_registry(tmp_path):
    path = tmp_path / "orders.xlsx"
    make_workbook(path)
    first = FlowPrefixRegistry(path)
    second = FlowPrefixRegistry(path)

    assert first.add_or_update("ceh01026", "con") == ("CEH01026", "CON")
    assert second.read() == {"CEH01026": "CON"}
    first.add_or_update("CEH01026", "PLG")

    workbook = load_workbook(path, read_only=True)
    try:
        sheet = workbook.worksheets[1]
        assert sheet.max_row == 2
        assert [sheet.cell(2, 1).value, sheet.cell(2, 2).value] == ["CEH01026", "PLG"]
    finally:
        workbook.close()


def test_first_prefix_creates_sheet2_when_workbook_only_has_sheet1(tmp_path):
    path = tmp_path / "orders.xlsx"
    workbook = Workbook()
    workbook.active.title = "Sheet1"
    workbook.save(path)

    FlowPrefixRegistry(path).add_or_update("CML00090", "GLD")

    workbook = load_workbook(path, read_only=True)
    try:
        assert workbook.sheetnames == ["Sheet1", "Prefixes"]
        sheet = workbook.worksheets[1]
        assert [sheet.cell(1, 1).value, sheet.cell(1, 2).value] == ["Part Number", "Prefix"]
        assert [sheet.cell(2, 1).value, sheet.cell(2, 2).value] == ["CML00090", "GLD"]
    finally:
        workbook.close()


def test_shared_prefix_applies_quantity_format_but_builtin_nomenclature_wins():
    catalogue = MaterialCatalogue(shared_prefixes={
        "CEH01026": "CON",
        "AE02909": "WRONG",
    })
    items = FlowSerialPolicy(catalogue).propose([
        line("CEH01026", 105),
        line("AE02909", 1, "Window Material"),
    ])

    assert items[0].status == ReviewStatus.RECOGNISED
    assert items[0].proposed_serials == ["50602018-CON-105"]
    assert items[1].proposed_serials == ["50602018-WIN-01"]


def test_ocu_description_still_expands_full_quantity_before_shared_prefix():
    catalogue = MaterialCatalogue(shared_prefixes={"CEH01026": "CON"})
    item = FlowSerialPolicy(catalogue).propose([
        line("CEH01026", 25, "OPTICAL CONVERTER UNIT")
    ])[0]

    assert len(item.proposed_serials) == 25
    assert item.proposed_serials[-1] == "50602018-OCU-25"
