from pathlib import Path

from app.phase2.extractors import (
    extract_costed_bom, extract_dca_pdf, parse_dca_pages, parse_dcl_pages, parse_drawing_pages,
)
from openpyxl import Workbook


class _LayoutPage:
    def __init__(self, text, tokens):
        self.text = text
        self.tokens = tokens

    def extract_text(self, visitor_text=None, **_kwargs):
        if visitor_text:
            for x, y, value in self.tokens:
                visitor_text(value, None, [1, 0, 0, 1, x, y], None, 10)
        return self.text


def test_dca_layout_extracts_ocu_and_in_ext_cabinet_colours(monkeypatch):
    page = _LayoutPage(
        "4.3 OCUs\n5.1 Control Cabinet\n5.1.10 Cabinet Colour",
        [
            (50, 700, "4.3 OCUs"), (400, 650, "Colour (RAL7035 std.)"),
            (420, 620, "RAL7038"), (50, 500, "4.4 OCU Numbering Scheme"),
            (50, 300, "5.1.10 Cabinet Colour"), (200, 300, "RAL7035"),
            (300, 300, "RAL9001"), (400, 300, "RAL7038"),
        ],
    )
    monkeypatch.setattr("app.phase2.extractors.PdfReader", lambda _path: type("R", (), {"pages": [page]})())

    result = extract_dca_pdf(Path("fixture.pdf"), "pdm_dca")

    assert [item.value for item in result.colours["OCU"]] == ["7038/7038"]
    assert [item.value for item in result.colours["RCC"]] == ["9001/7035"]


def test_dca_layout_preserves_mixed_ocu_colour_quantity_batches(monkeypatch):
    page = _LayoutPage(
        "4.3 OCUs\n4.4 OCU Numbering Scheme",
        [
            (50, 700, "4.3 OCUs"), (130, 660, "Qty"), (400, 660, "Colour (RAL7035 std.)"),
            (130, 630, "3"), (420, 630, "RAL7035"),
            (130, 600, "2"), (420, 600, "RAL7038"),
            (50, 500, "4.4 OCU Numbering Scheme"),
        ],
    )
    monkeypatch.setattr("app.phase2.extractors.PdfReader", lambda _path: type("R", (), {"pages": [page]})())

    result = extract_dca_pdf(Path("fixture.pdf"), "pdm_dca")

    assert [(quantity, colour) for quantity, colour, _evidence in result.colour_batches["OCU"]] == [
        (3, "7035/7035"), (2, "7038/7038"),
    ]
    assert "OCU" not in result.colours


def test_dca_layout_extracts_indoor_and_outdoor_ocu_quantity_batches(monkeypatch):
    page = _LayoutPage(
        "4.3 OCUs\n4.4 OCU Numbering Scheme",
        [
            (50, 700, "4.3 OCUs"), (130, 660, "Qty"), (300, 660, "Indoor/Outdoor"),
            (130, 630, "13"), (300, 630, "Indoor"),
            (130, 600, "2"), (300, 600, "Outdoor"),
            (50, 500, "4.4 OCU Numbering Scheme"),
        ],
    )
    monkeypatch.setattr("app.phase2.extractors.PdfReader", lambda _path: type("R", (), {"pages": [page]})())

    result = extract_dca_pdf(Path("fixture.pdf"), "pdm_dca")

    assert [(quantity, environment) for quantity, environment, _evidence in result.environment_batches["OCU"]] == [
        (13, "Indoor"), (2, "Outdoor"),
    ]


def test_dca_layout_ignores_base_colour_for_gddc(monkeypatch):
    page = _LayoutPage(
        "5.2 Gas Density Distribution Cabinet\n5.2.8 Cabinet Colour",
        [
            (50, 300, "5.2.8 Cabinet Colour"), (200, 300, "7038"),
            (300, 300, "7038"), (400, 300, "9005"),
        ],
    )
    monkeypatch.setattr("app.phase2.extractors.PdfReader", lambda _path: type("R", (), {"pages": [page]})())

    result = extract_dca_pdf(Path("fixture.pdf"), "gdm_dca")

    assert [item.value for item in result.colours["GDDC"]] == ["7038/7038"]


def test_dcl_extracts_commercial_customer_and_expands_only_clear_ocu_lists():
    page = """Order:50577444 PO Number:4508129178 Customer:Siemens Energy High Voltage Switchgear Ltd Contracts:Fullerton, Gareth
RADIAL CONTROL CABINET 50577444-RCC RCCIII 1
RADIAL DISTRIBUTION CABINET 50577444-RDC CMA 1
OPTICAL CONVERTER UNIT 50577444-OCU-01, 02 OCUIII 2
OPTICAL CONVERTER UNIT 50577444-OCU-11 OCUIII 1
"""
    result = parse_dcl_pages([page], "DCL.pdf", "B")

    assert [item.value for item in result.commercial_customers] == [
        "Siemens Energy High Voltage Switchgear Ltd"
    ]
    assert [item.value for item in result.serials["RCC"]] == ["50577444-CAB-RCC-01"]
    assert [item.value for item in result.serials["RDC"]] == ["50577444-CAB-RDC-01"]
    assert [item.value for item in result.serials["OCU"]] == [
        "50577444-OCU-01", "50577444-OCU-02", "50577444-OCU-11"
    ]
    assert all(item.location == "page 1" for values in result.serials.values() for item in values)


def test_dcl_does_not_expand_ambiguous_ranges():
    result = parse_dcl_pages(["OCU serials 50577444-OCU-01 to 11"], "DCL.pdf")
    assert [item.value for item in result.serials["OCU"]] == ["50577444-OCU-01"]


def test_dcl_expands_hyphenated_ocu_range_when_quantity_confirms_it():
    result = parse_dcl_pages([
        "2.1.22.0 OPTICAL CONVERTER UNIT 50584871-OCU-01-07 OCUIII-00411591 7"
    ], "50584871 DCL.pdf")

    assert [item.value for item in result.serials["OCU"]] == [
        "50584871-OCU-01", "50584871-OCU-02", "50584871-OCU-03",
        "50584871-OCU-04", "50584871-OCU-05", "50584871-OCU-06",
        "50584871-OCU-07",
    ]
    assert all(
        item.extraction_method in {
            "dcl_quantity_confirmed_serial_range",
            "dcl_quantity_confirmed_multiline_serial_list",
        }
        for item in result.serials["OCU"]
    )


def test_dcl_does_not_expand_hyphenated_ocu_range_when_quantity_disagrees():
    result = parse_dcl_pages([
        "OPTICAL CONVERTER UNIT 50584871-OCU-01-07 OCUIII-00411591 6"
    ], "50584871 DCL.pdf")

    assert [item.value for item in result.serials["OCU"]] == ["50584871-OCU-01"]


def test_dcl_quantity_confirmed_hyphenated_ranges_apply_to_other_cabinet_types():
    result = parse_dcl_pages([
        "DISTRIBUTION CABINETS 50584871-GDDC-01-04 CMA00310 4"
    ], "50584871 DCL.pdf")

    assert [item.value for item in result.serials["GDDC"]] == [
        "50584871-CAB-GDDC-01", "50584871-CAB-GDDC-02",
        "50584871-CAB-GDDC-03", "50584871-CAB-GDDC-04",
    ]


def test_dcl_expands_quantity_confirmed_multiline_ocu_lists_and_ranges():
    result = parse_dcl_pages(["""
3 3.1.26.0 OPTICAL CONVERTER UNIT 50564469-OCU-01,
03-09, 50, 52-58
OCUIII-00414370 16
3 3.1.43.0 OPTICAL CONVERTER UNIT 50564469-OCU-02,
51
OCUIII-00422877 2
"""], "50564469 DCL.pdf")

    assert [item.value for item in result.serials["OCU"]] == [
        "50564469-OCU-01", "50564469-OCU-03", "50564469-OCU-04",
        "50564469-OCU-05", "50564469-OCU-06", "50564469-OCU-07",
        "50564469-OCU-08", "50564469-OCU-09", "50564469-OCU-50",
        "50564469-OCU-52", "50564469-OCU-53", "50564469-OCU-54",
        "50564469-OCU-55", "50564469-OCU-56", "50564469-OCU-57",
        "50564469-OCU-58", "50564469-OCU-02", "50564469-OCU-51",
    ]


def test_dcl_expands_compact_quantity_confirmed_rdc_range():
    result = parse_dcl_pages([
        "VX25 Baying Cabinet 50564469-RDC1-4 CMA00310 4"
    ], "50564469 DCL.pdf")

    assert [item.value for item in result.serials["RDC"]] == [
        "50564469-CAB-RDC-01", "50564469-CAB-RDC-02",
        "50564469-CAB-RDC-03", "50564469-CAB-RDC-04",
    ]


def test_textless_dcl_is_flagged_instead_of_invented():
    result = parse_dcl_pages([""], "scan.pdf")
    assert not any(result.serials.values())
    assert [issue.code for issue in result.issues] == ["DCL_NO_SERIALS_EXTRACTED"]


def test_dca_extracts_only_explicit_quantities_colours_and_provenance():
    result = parse_dca_pages([
        "RCC Quantity: 1\nRDC quantity = 1\nOCU 01 to OCU 11\n"
        "RCC Colour: RAL 7038 / RAL 7038\nOCU Outdoor Finish: 9016/9016"
    ], "PDM DCA.pdf", "pdm", "B")
    assert [item.value for item in result.quantities["RCC"]] == ["1"]
    assert [item.value for item in result.quantities["RDC"]] == ["1"]
    assert [item.value for item in result.quantities["OCU"]] == ["11"]
    assert result.colours["RCC"][0].value == "7038/7038"
    assert result.colours["OCU OUTDOOR"][0].value == "9016/9016"
    assert result.quantities["OCU"][0].location == "page 1"
    assert result.number_sequences["OCU"][0].value == ",".join(str(i) for i in range(1, 12))


def test_dca_preserves_non_continuous_equipment_numbers():
    result = parse_dca_pages([
        "OCU Numbers: 1, 3, 7\nRDC Numbering Scheme: 01, 04-05"
    ], "PDM DCA.pdf", "pdm", "B")
    assert result.number_sequences["OCU"][0].value == "1,3,7"
    assert result.number_sequences["RDC"][0].value == "1,4,5"
    assert result.quantities["OCU"][0].value == "3"


def test_dca_conflicting_explicit_values_are_blocking():
    result = parse_dca_pages([
        "GDDC quantity: 1\nGDDC quantity: 2"
    ], "GDM DCA.pdf", "gdm", "B")
    assert {item.value for item in result.quantities["GDDC"]} == {"1", "2"}
    assert [issue.code for issue in result.issues] == ["DCA_VALUE_CONFLICT"]


def test_dca_does_not_guess_from_unlabelled_table_numbers():
    result = parse_dca_pages([
        "RCC RDC GDDC 1 1 1 7038 9005\n9.1 Ethernet Switch-RCC QTY 0\nPCU QTY 0"
    ], "DCA.pdf", "pdm")
    assert result.quantities == {}
    assert result.colours == {}
    assert [issue.code for issue in result.issues] == ["DCA_NO_EXPLICIT_VALUES"]


def test_costed_bom_retains_relevant_rows_as_supporting_evidence(tmp_path):
    path = tmp_path / "BOM.xlsx"
    workbook = Workbook(); sheet = workbook.active; sheet.title = "BOM"
    sheet.append(["Order Number", "Component Item Number", "Component Description", "Component Required Qty"])
    sheet.append(["50577444", "CMA1", "RCC cabinet RAL7038", 1])
    sheet.append(["50577444", "SCREW", "M4 screw", 40])
    sheet.append(["999", "CMA2", "RDC cabinet", 1])
    workbook.save(path)
    result = extract_costed_bom(path, "50577444")
    assert len(result.evidence) == 1
    assert result.evidence[0].location == "BOM!2"
    assert result.evidence[0].validation_state == "supporting_only"


def test_drawing_maps_colour_only_when_page_is_unambiguous():
    result = parse_drawing_pages([
        "Cabinet 50577444-RCC finish RAL 7038 / RAL 7038",
        "50577444-OCU-01 and 50577444-OCU-02 finishes 7038/7038 and 9016/9016",
    ], "GA.pdf", "50577444")
    assert result.serial_colours["50577444-cab-rcc-01"][0].value == "7038/7038"
    assert "50577444-ocu-01" not in result.serial_colours
    assert [issue.code for issue in result.issues] == ["DRAWING_COLOUR_AMBIGUOUS"]


def test_drawing_maps_separately_labelled_internal_external_finish_and_ignores_plinth():
    result = parse_drawing_pages([
        "50591150-CAB-GDDC-01\n"
        "FINISH:-\n"
        "EXTERNAL SURFACES:\nALL EXTERNAL SURFACES - RAL 7035 PALE GREY\n"
        "PLINTH - RAL 9005 DEEP BLACK\n"
        "INTERNAL SURFACES:\nALL PAINTED INTERNAL SURFACES - RAL 7035 PALE GREY"
    ], "GA.pdf", "50591150")

    evidence = result.serial_colours["50591150-cab-gddc-01"]
    assert [item.value for item in evidence] == ["7035/7035"]


def test_drawing_maps_compact_ocr_finish_to_identified_gddc_type():
    result = parse_drawing_pages([
        "GAS DENSITY DISTRIBUTION CABINETS "
        "EXTERNALSURFACES: ALL EXTERNAL SURFACES -RAL 7035 PALE GREY "
        "PLINTH -RAL9005 DEEP BLACK INTERNALSURFACES: "
        "ALLPAINTEDINTERNALSURFACES RAL7035 PALE GREY"
    ], "GA.pdf", "50591150")

    assert [item.value for item in result.type_colours["GDDC"]] == ["7035/7035"]


def test_drawing_does_not_treat_title_block_phone_number_as_colour():
    result = parse_drawing_pages([
        "GAS DENSITY DISTRIBUTION CABINET 1 (SERIAL NO: 50591150-GDDC1) "
        "Tel: 24537643/24537647 Fax: 24537625"
    ], "GA.pdf", "50591150")

    assert result.serial_colours == {}


def test_drawing_normalizes_legacy_sui_serial_to_siu():
    result = parse_drawing_pages([
        "50591150-SUI-01 finish RAL 7035 / RAL 7035"
    ], "GA.pdf", "50591150")
    assert "50591150-siu-01" in result.serial_colours


def test_drawing_detects_explicit_combined_pdm_gdm_distribution_cabinet():
    result = parse_drawing_pages([
        "PDM/GDM DISTRIBUTION CABINET WIRING\nOther drawing details"
    ], "W01549 Rev A.pdf", "50577444")

    assert len(result.combined_rdc_gddc) == 1
    assert result.combined_rdc_gddc[0].field == "combined_rdc_gddc_system"


def test_dcl_expands_compact_gddc_serial_range_and_quantity():
    result = parse_dcl_pages([
        "33 33.1 Cabinet 50538114-GDDC1 to 4 CMA00310 4"
    ], "50538114 DCL.pdf")

    assert [item.value for item in result.serials["GDDC"]] == [
        "50538114-CAB-GDDC-01", "50538114-CAB-GDDC-02",
        "50538114-CAB-GDDC-03", "50538114-CAB-GDDC-04",
    ]


def test_dcl_normalizes_rcc_and_cbm_to_numbered_cabinet_format():
    result = parse_dcl_pages([
        "Cabinet 50538114-RCC qty 1\nCabinets 50538114-CBM1 to 3 qty 3"
    ], "50538114 DCL.pdf")

    assert [item.value for item in result.serials["RCC"]] == ["50538114-CAB-RCC-01"]
    assert [item.value for item in result.serials["CBM"]] == [
        "50538114-CAB-CBM-01", "50538114-CAB-CBM-02", "50538114-CAB-CBM-03",
    ]


def test_dcl_normalizes_compact_rdc_and_gddc_serials():
    result = parse_dcl_pages([
        "Cabinet 50591150-RDC1 qty 1\nCabinet 50591150-GDDC2 qty 1"
    ], "50591150 DCL.pdf")
    assert [item.value for item in result.serials["RDC"]] == ["50591150-CAB-RDC-01"]
    assert [item.value for item in result.serials["GDDC"]] == ["50591150-CAB-GDDC-02"]


def test_dcl_normalizes_mcms_to_cabinet_format():
    result = parse_dcl_pages([
        "Motor Control Management System 50550775-MCMS1 to 2 qty 2"
    ], "50550775 DCL.pdf")

    assert [item.value for item in result.serials["MCMS"]] == [
        "50550775-CAB-MCMS-01", "50550775-CAB-MCMS-02",
    ]


def test_dcl_preserves_explicit_new_cabinet_number():
    result = parse_dcl_pages([
        "MCMS cabinet 50550775-CAB-MCMS-03 qty 1"
    ], "50550775 DCL.pdf")

    assert [item.value for item in result.serials["MCMS"]] == [
        "50550775-CAB-MCMS-03",
    ]
