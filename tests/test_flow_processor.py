from decimal import Decimal

from openpyxl import Workbook

from app.flow.dcl_parser import parse_flow_dcl_pages
from app.flow.processor import FlowProcessor


def workbook(path, category="Flow", so="50565048"):
    book = Workbook(); sheet = book.active; sheet.title = "Sheet1"
    sheet.append(["Class", "Stream", "Order", "Term", "Customer"])
    sheet.append([category, "DMS", so, "FCA", "Workbook Customer"])
    book.save(path)


def test_flow_dcl_parser_keeps_line_provenance_and_customer():
    result = parse_flow_dcl_pages([
        "Order:50565048 Customer:Commercial Customer Ltd Contracts:Name\n"
        "1.1 Window Material (Metric) AE02909 12 Each"
    ], "DCL.pdf", "50565048")
    assert result.issues == []
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.customer == "Commercial Customer Ltd"
    assert line.part_code == "AE02909"
    assert line.quantity == Decimal("12")
    assert line.page == 1 and line.source_file == "DCL.pdf"


def test_flow_dcl_parser_accepts_unfamiliar_part_prefixes():
    result = parse_flow_dcl_pages([
        "Order:50602018 Customer:Willowglen Service Pte Ltd Contracts:Fullerton\n"
        "2 2.1.19 CEH01026, CNT-400 N-TYPE Male straight connector CEH01026 105"
    ], "DCL.pdf", "50602018")

    assert len(result.lines) == 1
    assert result.lines[0].part_code == "CEH01026"
    assert result.lines[0].quantity == Decimal("105")


def test_flow_processor_reads_dcl_only_and_ignores_conflicting_other_folders(tmp_path, monkeypatch):
    orders = tmp_path / "orders.xlsx"; workbook(orders)
    root = tmp_path / "jobs"; dcl = root / "50565048" / "DCL"; dcl.mkdir(parents=True)
    (root / "50565048" / "Specs").mkdir()
    (root / "50565048" / "Specs" / "old RCC.pdf").write_bytes(b"must never be read")
    dcl_pdf = dcl / "DCL.pdf"; dcl_pdf.write_bytes(b"fixture")

    class Extraction:
        issues = []
        lines = parse_flow_dcl_pages([
            "Order:50565048 Customer:C Ltd Contracts:X\n1.1 Window Material AE02909 12 Each"
        ], "DCL.pdf", "50565048").lines

    seen = []
    monkeypatch.setattr("app.flow.processor.extract_flow_dcl_pdf", lambda path, so: seen.append(path) or Extraction())
    run = FlowProcessor(orders, root).prepare("50565048")
    assert seen == [dcl_pdf]
    assert len(run.review_items[0].proposed_serials) == 12
    assert all("RCC" not in serial for serial in run.review_items[0].proposed_serials)


def test_project_order_is_ineligible_for_flow(tmp_path):
    orders = tmp_path / "orders.xlsx"; workbook(orders, category="Project")
    root = tmp_path / "jobs"; root.mkdir()
    try:
        FlowProcessor(orders, root).prepare("50565048")
        assert False, "Project order must not enter Flow processor"
    except Exception as exc:
        assert "not an eligible DMS Flow" in str(exc)
