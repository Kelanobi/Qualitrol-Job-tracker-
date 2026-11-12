from app.flow.dcl_parser import parse_flow_dcl_pages
from app.flow.serial_policy import FlowSerialPolicy


def test_catalogued_cable_quantity_without_printed_unit_becomes_one_metre_row():
    text = (
        "Despatch Checklist (DCL)\n"
        "Order:50605782 Customer:SM Electric Contracts:Bradley\n"
        "Line Line Number Description Serial Number Part Code Qty\n"
        "1 1.1 CFL00335, UHF Cable, CNT 50 OHM Braided Coaxial Cable CFL00335 300"
    )
    extraction = parse_flow_dcl_pages([text], "50605782 - Despatch Checklist (Rev01).pdf", "50605782")
    items = FlowSerialPolicy().propose(extraction.lines)
    assert len(items) == 1
    assert items[0].proposed_serials == ["50605782-CFL00335-300m"]
    assert items[0].status.value == "recognised"
