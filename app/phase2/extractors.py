from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from openpyxl import load_workbook

from app.phase2.models import Evidence, ValidationIssue, IssueSeverity


SERIAL_TYPES = ("RCC", "RDC", "GDDC", "CBM", "MCMS", "OCU", "ICU", "PCU", "SIU", "SDI", "SER")
FULL_SERIAL = re.compile(
    r"\b(?P<so>\d+)-(?:CAB-)?(?P<kind>RCC|RDC|GDDC|CBM|MCMS|PCU|SIU|SUI|SDI|SER|(?:OCU|ICU)-\d{1,})"
    r"-?(?P<cabinet_index>\d+)?\b",
    re.IGNORECASE,
)
CUSTOMER = re.compile(r"\bCustomer\s*:\s*(.+?)(?=\s+Contracts?\s*:|\s+Project\s+Substation|$)", re.IGNORECASE)


@dataclass
class DclExtraction:
    commercial_customers: list[Evidence] = field(default_factory=list)
    serials: dict[str, list[Evidence]] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class DcaExtraction:
    quantities: dict[str, list[Evidence]] = field(default_factory=dict)
    number_sequences: dict[str, list[Evidence]] = field(default_factory=dict)
    colours: dict[str, list[Evidence]] = field(default_factory=dict)
    colour_batches: dict[str, list[tuple[int, str, Evidence]]] = field(default_factory=dict)
    environment_batches: dict[str, list[tuple[int, str, Evidence]]] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class BomExtraction:
    evidence: list[Evidence] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class DrawingExtraction:
    serial_colours: dict[str, list[Evidence]] = field(default_factory=dict)
    type_colours: dict[str, list[Evidence]] = field(default_factory=dict)
    combined_rdc_gddc: list[Evidence] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


def _serial_type(serial: str) -> str:
    tail = serial.split("-", 1)[1].upper()
    if tail.startswith("CAB-"):
        tail = tail[4:]
    if tail.startswith(("OCU-", "ICU-")):
        return tail[:3]
    numbered_cabinet = re.fullmatch(r"(RCC|RDC|GDDC|CBM|MCMS)-?\d+", tail)
    kind = numbered_cabinet.group(1) if numbered_cabinet else tail
    return "SIU" if kind.startswith("SUI") else kind


def _normalize_production_serial(serial: str) -> str:
    value = re.sub(r"-SUI-(?=\d+\b)", "-SIU-", serial.upper())
    match = re.fullmatch(
        r"(?P<so>\d+)-(?:CAB-)?(?P<kind>RCC|RDC|GDDC|CBM|MCMS)-?(?P<number>\d+)?",
        value,
    )
    if not match:
        return value
    return (
        f"{match.group('so')}-CAB-{match.group('kind')}-"
        f"{int(match.group('number') or 1):02d}"
    )


def _expand_unambiguous_ocu_suffixes(line: str, first_serial: str) -> list[str]:
    """Expand `SO-OCU-01, 02, 07` only; no ranges or inferred cabinet serials."""
    if "-OCU-" not in first_serial.upper():
        return [first_serial]
    start = line.casefold().find(first_serial.casefold())
    tail = line[start + len(first_serial):] if start >= 0 else ""
    suffix_text = re.match(r"(?:\s*,\s*\d{1,})+", tail)
    if not suffix_text:
        return [first_serial]
    prefix, first_suffix = first_serial.rsplit("-", 1)
    width = max(2, len(first_suffix))
    result = [f"{prefix}-{int(first_suffix):0{width}d}"]
    result.extend(
        f"{prefix}-{int(value):0{width}d}"
        for value in re.findall(r"\d{1,}", suffix_text.group(0))
    )
    return result


def _expand_numeric_serial_list(value: str) -> list[int] | None:
    """Expand comma-separated suffixes such as `01, 03-09, 50, 52-58`."""
    normalized = re.sub(r"-\s+", "-", value.strip())
    numbers: list[int] = []
    for token in normalized.split(","):
        token = token.strip()
        single = re.fullmatch(r"\d+", token)
        serial_range = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if single:
            numbers.append(int(token))
        elif serial_range:
            first, last = map(int, serial_range.groups())
            if last < first or last - first > 500:
                return None
            numbers.extend(range(first, last + 1))
        else:
            return None
    return numbers


def parse_dcl_pages(pages: list[str], source_file: str, revision: str | None = None) -> DclExtraction:
    result = DclExtraction(serials={kind: [] for kind in SERIAL_TYPES})
    seen: set[str] = set()
    for page_number, text in enumerate(pages, 1):
        page_location = f"page {page_number}"

        # DCL tables may wrap a long OCU serial list over several PDF text
        # lines. Reconstruct the complete table cell and trust it only when
        # its expanded suffix count equals the row's Qty value.
        for match in re.finditer(
            r"OPTICAL\s+CONVERTER\s+UNIT\s+"
            r"(?P<so>\d+)-OCU-(?P<suffixes>[\d,\s-]+?)\s+"
            r"OCU[A-Z0-9]*-\d+\s+(?P<quantity>\d+)\b",
            text, re.IGNORECASE,
        ):
            suffixes = _expand_numeric_serial_list(match.group("suffixes"))
            if not suffixes or len(suffixes) != int(match.group("quantity")) or len(set(suffixes)) != len(suffixes):
                continue
            width = max(2, *(len(token) for token in re.findall(r"\d+", match.group("suffixes"))))
            for number in suffixes:
                serial = f"{match.group('so')}-OCU-{number:0{width}d}"
                if serial.casefold() in seen:
                    continue
                seen.add(serial.casefold())
                result.serials["OCU"].append(Evidence(
                    field="production_serial", value=serial, source_file=source_file,
                    location=page_location, extraction_method="dcl_quantity_confirmed_multiline_serial_list",
                    confidence=0.99, revision=revision, validation_state="verified",
                ))

        for line in text.splitlines():
            customer = CUSTOMER.search(line)
            if customer:
                value = customer.group(1).strip()
                if value and all(item.value.casefold() != value.casefold() for item in result.commercial_customers):
                    result.commercial_customers.append(Evidence(
                        field="commercial_customer", value=value, source_file=source_file,
                        location=page_location, extraction_method="dcl_label", confidence=0.98,
                        revision=revision, validation_state="candidate",
                    ))
            range_matches = list(re.finditer(
                r"\b(?P<so>\d+)-(?:CAB-)?(?P<kind>RCC|RDC|GDDC|CBM|MCMS)\s*-?\s*(?P<first>\d+)\s+to\s+(?P<last>\d+)\b",
                line, re.IGNORECASE,
            ))
            range_spans = [match.span() for match in range_matches]
            for match in range_matches:
                first, last = int(match.group("first")), int(match.group("last"))
                if last < first or last - first > 100:
                    result.issues.append(ValidationIssue(
                        code="DCL_SERIAL_RANGE_INVALID", severity=IssueSeverity.BLOCKING,
                        message=f"Invalid serial range {match.group(0)!r} in {source_file}.",
                    ))
                    continue
                width = max(2, len(match.group("first")), len(match.group("last")))
                kind = match.group("kind").upper()
                for number in range(first, last + 1):
                    # Normalize compact DCL notation such as GDDC1 to 4 to the
                    # approved production serial format GDDC-01 ... GDDC-04.
                    serial = f"{match.group('so')}-CAB-{kind}-{number:0{width}d}"
                    if serial.casefold() in seen:
                        continue
                    seen.add(serial.casefold())
                    result.serials[kind].append(Evidence(
                        field="production_serial", value=serial, source_file=source_file,
                        location=page_location, extraction_method="dcl_explicit_serial_range",
                        confidence=0.99, revision=revision, validation_state="verified",
                    ))

            # Compact cabinet ranges also occur as SO-RDC1-4 (without `to`).
            for match in re.finditer(
                r"\b(?P<so>\d+)-(?:CAB-)?(?P<kind>RCC|RDC|GDDC|CBM|MCMS)-?(?P<first>\d+)-(?P<last>\d+)\b(?P<rest>.*)$",
                line, re.IGNORECASE,
            ):
                first, last = int(match.group("first")), int(match.group("last"))
                quantities = [int(value) for value in re.findall(r"\b\d+\b", match.group("rest"))]
                expected_quantity = last - first + 1
                if last < first or last - first > 100 or not quantities or quantities[-1] != expected_quantity:
                    continue
                range_spans.append(match.span())
                kind = match.group("kind").upper()
                width = max(2, len(match.group("first")), len(match.group("last")))
                for number in range(first, last + 1):
                    serial = f"{match.group('so')}-CAB-{kind}-{number:0{width}d}"
                    if serial.casefold() in seen:
                        continue
                    seen.add(serial.casefold())
                    result.serials[kind].append(Evidence(
                        field="production_serial", value=serial, source_file=source_file,
                        location=page_location, extraction_method="dcl_quantity_confirmed_serial_range",
                        confidence=0.99, revision=revision, validation_state="verified",
                    ))

            # Some DCL rows compact a serial range as SO-OCU-01-07. Expand it
            # only when the row's final quantity agrees with the range size.
            # This prevents a part-code number elsewhere on the row from being
            # mistaken for an OCU range.
            for match in re.finditer(
                r"\b(?P<so>\d+)-(?:CAB-)?(?P<kind>RCC|RDC|GDDC|CBM|MCMS|OCU)-(?P<first>\d{1,})-(?P<last>\d{1,})\b(?P<rest>.*)$",
                line, re.IGNORECASE,
            ):
                first, last = int(match.group("first")), int(match.group("last"))
                quantities = [int(value) for value in re.findall(r"\b\d+\b", match.group("rest"))]
                expected_quantity = last - first + 1
                if last < first or last - first > 100 or not quantities or quantities[-1] != expected_quantity:
                    continue
                range_spans.append(match.span())
                width = max(2, len(match.group("first")), len(match.group("last")))
                kind = match.group("kind").upper()
                for number in range(first, last + 1):
                    serial = (
                        f"{match.group('so')}-CAB-{kind}-{number:0{width}d}"
                        if kind in {"RCC", "RDC", "GDDC", "CBM", "MCMS"}
                        else f"{match.group('so')}-{kind}-{number:0{width}d}"
                    )
                    if serial.casefold() in seen:
                        continue
                    seen.add(serial.casefold())
                    result.serials[kind].append(Evidence(
                        field="production_serial", value=serial, source_file=source_file,
                        location=page_location, extraction_method="dcl_quantity_confirmed_serial_range",
                        confidence=0.99, revision=revision, validation_state="verified",
                    ))
            matches = [
                match for match in FULL_SERIAL.finditer(line)
                if not any(start <= match.start() < end for start, end in range_spans)
            ]
            for match in matches:
                kind = match.group("kind").upper()
                if kind in {"RCC", "RDC", "GDDC", "CBM", "MCMS"}:
                    index = int(match.group("cabinet_index") or 1)
                    serial = f"{match.group('so')}-CAB-{kind}-{index:02d}"
                elif kind.startswith("OCU-"):
                    # Do not absorb a second suffix from an unverified range
                    # such as OCU-01-07 when its row quantity disagrees.
                    serial = f"{match.group('so')}-{kind}"
                else:
                    serial = match.group(0).upper()
                candidates = _expand_unambiguous_ocu_suffixes(line, serial)
                for candidate in candidates:
                    if candidate.casefold() in seen:
                        continue
                    seen.add(candidate.casefold())
                    kind = _serial_type(candidate)
                    result.serials[kind].append(Evidence(
                        field="production_serial", value=candidate, source_file=source_file,
                        location=page_location, extraction_method="dcl_explicit_serial",
                        confidence=0.99, revision=revision, validation_state="verified",
                    ))
    if not any(result.serials.values()):
        result.issues.append(ValidationIssue(
            code="DCL_NO_SERIALS_EXTRACTED", severity=IssueSeverity.WARNING,
            message=f"No supported production serials could be extracted from {source_file}.",
        ))
    return result


def extract_dcl_pdf(path: Path, revision: str | None = None) -> DclExtraction:
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        return DclExtraction(issues=[ValidationIssue(
            code="DCL_PDF_PARSE_ERROR", severity=IssueSeverity.BLOCKING,
            message=f"Could not parse {path.name}: {exc}",
        )])
    result = parse_dcl_pages(pages, path.name, revision)
    if pages and not any(page.strip() for page in pages):
        result.issues.append(ValidationIssue(
            code="DCL_IMAGE_ONLY", severity=IssueSeverity.WARNING,
            message=f"{path.name} contains no extractable text and needs manual review or approved OCR.",
        ))
    return result


def _add_unique_evidence(target: dict[str, list[Evidence]], key: str, evidence: Evidence) -> None:
    values = target.setdefault(key, [])
    if not any(item.value == evidence.value and item.source_file == evidence.source_file for item in values):
        values.append(evidence)


def parse_dca_pages(
    pages: list[str], source_file: str, document_family: str, revision: str | None = None,
) -> DcaExtraction:
    """Parse only explicit DCA statements; table-position guesses are intentionally excluded."""
    result = DcaExtraction(quantities={}, number_sequences={}, colours={})
    family = document_family.casefold()
    for page_number, text in enumerate(pages, 1):
        location = f"page {page_number}"
        quantity_patterns: list[tuple[str, str]] = [
            ("RCC", r"^[ \t]*(?:RCC|Radial Control Cabinet)\s+(?:quantity|qty|count|number of cabinets?)\s*[:=]?\s*(\d+)"),
            ("RDC", r"^[ \t]*(?:RDC|Radial Distribution Cabinet)\s+(?:quantity|qty|count|number of cabinets?)\s*[:=]?\s*(\d+)"),
            ("GDDC", r"^[ \t]*(?:GDDC|Gas Density (?:Control|Distribution) Cabinet)\s+(?:quantity|qty|count|number of cabinets?)\s*[:=]?\s*(\d+)"),
            ("CBM", r"^[ \t]*(?:CBM|CBM Cabinet)\s+(?:quantity|qty|count|number of cabinets?)\s*[:=]?\s*(\d+)"),
            ("MCMS", r"^[ \t]*(?:MCMS|MCMS Cabinet)\s+(?:quantity|qty|count|number of cabinets?)\s*[:=]?\s*(\d+)"),
            ("OCU", r"^[ \t]*Total OCU\s*[:=]?\s*(\d+)"),
        ]
        for equipment_type, pattern in quantity_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
                _add_unique_evidence(result.quantities, equipment_type, Evidence(
                    field=f"{equipment_type.lower()}_quantity", value=str(int(match.group(1))),
                    source_file=source_file, location=location, extraction_method="dca_explicit_label",
                    confidence=0.96, revision=revision,
                ))

        # Explicit numbering statements are production identity evidence even
        # when the DCL omits the full SO-prefixed serial. Preserve the exact
        # detected numbers (including gaps) for every supported equipment type.
        numbered_types = "RCC|RDC|GDDC|CBM|MCMS|OCU|PCU|SIU|SUI|SDI|SER"
        for match in re.finditer(
            rf"\b(?P<kind>{numbered_types})\s*0*(?P<first>\d+)\s+to\s+"
            rf"(?:(?P=kind)\s*)?0*(?P<last>\d+)\b",
            text, re.IGNORECASE,
        ):
            first, last = int(match.group("first")), int(match.group("last"))
            if last < first or last - first > 500:
                continue
            kind = match.group("kind").upper().replace("SUI", "SIU")
            numbers = list(range(first, last + 1))
            sequence = ",".join(str(number) for number in numbers)
            _add_unique_evidence(result.number_sequences, kind, Evidence(
                field=f"{kind.lower()}_number_sequence", value=sequence,
                source_file=source_file, location=location,
                extraction_method="dca_explicit_numbering_scheme", confidence=0.99,
                revision=revision, validation_state="verified",
            ))
            _add_unique_evidence(result.quantities, kind, Evidence(
                field=f"{kind.lower()}_quantity", value=str(len(numbers)), source_file=source_file,
                location=location, extraction_method="dca_numbering_scheme", confidence=0.98,
                revision=revision,
            ))
        for match in re.finditer(
            rf"\b(?P<kind>{numbered_types})(?:\s+(?:numbers?|numbering(?:\s+scheme)?))?\s*[:=]\s*"
            r"(?P<numbers>\d+(?:\s*(?:,|-)\s*\d+)+)",
            text, re.IGNORECASE,
        ):
            numbers = _expand_numeric_serial_list(match.group("numbers"))
            if not numbers or len(set(numbers)) != len(numbers):
                continue
            kind = match.group("kind").upper().replace("SUI", "SIU")
            sequence = ",".join(str(number) for number in numbers)
            _add_unique_evidence(result.number_sequences, kind, Evidence(
                field=f"{kind.lower()}_number_sequence", value=sequence,
                source_file=source_file, location=location,
                extraction_method="dca_explicit_numbering_list", confidence=0.99,
                revision=revision, validation_state="verified",
            ))
            _add_unique_evidence(result.quantities, kind, Evidence(
                field=f"{kind.lower()}_quantity", value=str(len(numbers)), source_file=source_file,
                location=location, extraction_method="dca_numbering_list", confidence=0.98,
                revision=revision,
            ))

        colour_pattern = re.compile(
            r"\b(RCC|RDC|GDDC|CBM|MCMS|OCU(?:\s+(?:indoor|outdoor))?)\s+(?:cabinet\s+)?"
            r"(?:colour|color|finish)\s*[:=]?\s*(?:RAL\s*)?(\d{4})\s*[/,]\s*(?:RAL\s*)?(\d{4})",
            re.IGNORECASE,
        )
        for match in colour_pattern.finditer(text):
            key = " ".join(match.group(1).upper().split())
            value = f"{match.group(2)}/{match.group(3)}"
            _add_unique_evidence(result.colours, key, Evidence(
                field="cabinet_colour_in_ext", value=value, source_file=source_file,
                location=location, extraction_method="dca_explicit_finish", confidence=0.96,
                revision=revision,
            ))

    for field_name, values in (*result.quantities.items(), *result.colours.items()):
        distinct = {item.value for item in values}
        if len(distinct) > 1:
            result.issues.append(ValidationIssue(
                code="DCA_VALUE_CONFLICT", severity=IssueSeverity.BLOCKING,
                message=f"DCA evidence conflicts for {field_name}: {sorted(distinct)}.",
                equipment_type=field_name.split()[0],
            ))
    if not result.quantities and not result.number_sequences and not result.colours:
        result.issues.append(ValidationIssue(
            code="DCA_NO_EXPLICIT_VALUES", severity=IssueSeverity.WARNING,
            message=f"No safely labelled equipment quantities or finishes were extracted from {source_file}.",
        ))
    return result


def extract_dca_pdf(path: Path, document_family: str, revision: str | None = None) -> DcaExtraction:
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        return DcaExtraction(issues=[ValidationIssue(
            code="DCA_PDF_PARSE_ERROR", severity=IssueSeverity.BLOCKING,
            message=f"Could not parse {path.name}: {exc}",
        )])
    result = parse_dca_pages(pages, path.name, document_family, revision)
    layout_found = False
    for page_number, page in enumerate(reader.pages, 1):
        tokens: list[tuple[float, float, str]] = []

        def visitor(text, _cm, tm, _font, _size):
            value = text.strip()
            if value:
                tokens.append((float(tm[4]), float(tm[5]), value))

        try:
            page.extract_text(visitor_text=visitor)
        except Exception:
            continue
        page_text = page.extract_text() or ""
        page_folded = page_text.casefold()

        # Section 4.3 is an OCU table. Read only actual RAL value cells between
        # the 4.3 and 4.4 headings; the header's `(RAL7035 std.)` is guidance,
        # not a selected colour. Multiple distinct selections are ambiguous and
        # are intentionally left blank.
        ocu_heading = next(
            ((x, y) for x, y, value in tokens if re.search(r"\b4\.3\s+OCUs?\b", value, re.IGNORECASE)),
            None,
        )
        if ocu_heading:
            lower_heading_y = max(
                (
                    y for _x, y, value in tokens
                    if y < ocu_heading[1] and re.search(r"\b4\.[4-9]\b", value, re.IGNORECASE)
                ),
                default=-1.0,
            )
            section_tokens = [
                (x, y, value) for x, y, value in tokens
                if lower_heading_y < y < ocu_heading[1]
            ]
            colour_header = next(
                ((x, y) for x, y, value in section_tokens if "colour" in value.casefold() or "color" in value.casefold()),
                None,
            )
            qty_header = next(
                ((x, y) for x, y, value in section_tokens if re.search(r"\bqty\b", value, re.IGNORECASE)),
                None,
            )
            environment_header = next(
                ((x, y) for x, y, value in section_tokens if re.search(r"indoor\s*/\s*outdoor", value, re.IGNORECASE)),
                None,
            )
            colour_cells = [
                (x, y, match.group(1))
                for x, y, value in section_tokens
                for match in [re.fullmatch(r"\s*(?:RAL\s*)?(\d{4})\s*", value, re.IGNORECASE)]
                if match and (not colour_header or x >= colour_header[0] - 35)
            ]
            ocu_rals = {ral for _x, _y, ral in colour_cells}
            batches: list[tuple[int, str, Evidence]] = []
            environment_batches: list[tuple[int, str, Evidence]] = []
            if qty_header:
                for _colour_x, colour_y, ral in sorted(colour_cells, key=lambda item: -item[1]):
                    quantities = []
                    for value_x, value_y, value in section_tokens:
                        if abs(value_y - colour_y) > 2.0 or abs(value_x - qty_header[0]) > 35:
                            continue
                        match = re.fullmatch(r"\s*(\d+)(?:\s+\d+)?\s*", value)
                        if match:
                            quantities.append(int(match.group(1)))
                    if len(quantities) == 1 and quantities[0] > 0:
                        evidence = Evidence(
                            field="ocu_colour_batch", value=f"{ral}/{ral}",
                            source_file=path.name, location=f"page {page_number}",
                            extraction_method="dca_layout_ocu_quantity_colour_row",
                            confidence=0.99, revision=revision, validation_state="verified",
                        )
                        batches.append((quantities[0], f"{ral}/{ral}", evidence))
            if qty_header and environment_header:
                for environment_x, environment_y, value in sorted(section_tokens, key=lambda item: -item[1]):
                    environment_match = re.fullmatch(r"\s*(indoor|outdoor)\s*", value, re.IGNORECASE)
                    if not environment_match or abs(environment_x - environment_header[0]) > 55:
                        continue
                    quantities = []
                    for value_x, value_y, quantity_value in section_tokens:
                        if abs(value_y - environment_y) > 2.0 or abs(value_x - qty_header[0]) > 35:
                            continue
                        quantity_match = re.fullmatch(r"\s*(\d+)(?:\s+\d+)?\s*", quantity_value)
                        if quantity_match:
                            quantities.append(int(quantity_match.group(1)))
                    if len(quantities) == 1 and quantities[0] > 0:
                        environment = environment_match.group(1).title()
                        evidence = Evidence(
                            field="ocu_environment_batch", value=environment,
                            source_file=path.name, location=f"page {page_number}",
                            extraction_method="dca_layout_ocu_quantity_environment_row",
                            confidence=0.99, revision=revision, validation_state="verified",
                        )
                        environment_batches.append((quantities[0], environment, evidence))
            if environment_batches:
                result.environment_batches.setdefault("OCU", []).extend(environment_batches)
                layout_found = True
            if batches and len({colour for _qty, colour, _evidence in batches}) > 1:
                result.colour_batches.setdefault("OCU", []).extend(batches)
                layout_found = True
            if len(ocu_rals) == 1:
                ral = next(iter(ocu_rals))
                _add_unique_evidence(result.colours, "OCU", Evidence(
                    field="cabinet_colour_in_ext", value=f"{ral}/{ral}",
                    source_file=path.name, location=f"page {page_number}",
                    extraction_method="dca_layout_ocu_table", confidence=0.99, revision=revision,
                ))
                layout_found = True

        for x, y, label in tokens:
            label_folded = label.casefold()
            equipment_type = None
            if label_folded.startswith("5.1"):
                equipment_type = "RCC" if (
                    document_family.casefold().startswith("pdm")
                    or "5.1 control cabinet" in page_folded
                ) else None
            elif label_folded.startswith("5.2"):
                equipment_type = "RDC" if (
                    document_family.casefold().startswith("pdm")
                    or "5.2 distribution cabinet" in page_folded
                ) else "GDDC"
            if not equipment_type:
                continue
            same_row_tokens = sorted(
                    (
                        (value_x, value_y, value) for value_x, value_y, value in tokens
                        if value_x > x + 80 and abs(value_y - y) <= 2.0
                    ),
                    key=lambda item: item[0],
                )
            same_row = [value for _value_x, _value_y, value in same_row_tokens]
            if "number of cabinets" in label_folded:
                numeric = next((value for value in same_row if re.fullmatch(r"\d+", value)), None)
                if numeric is not None:
                    _add_unique_evidence(result.quantities, equipment_type, Evidence(
                        field=f"{equipment_type.lower()}_quantity", value=str(int(numeric)),
                        source_file=path.name, location=f"page {page_number}",
                        extraction_method="dca_layout_label", confidence=0.98, revision=revision,
                    ))
                    layout_found = True
            if "cabinet colour" in label_folded or "cabinet color" in label_folded:
                ral_values: list[str] = []
                for value in same_row:
                    ral_values.extend(re.findall(r"\b(?:RAL\s*)?(\d{4})\b", value, re.IGNORECASE))
                if len(ral_values) >= 2:
                    # DCA tables present Outside, Inside, Base from left to
                    # right; Smartsheet stores Colour In/Ext, so reverse only
                    # the first two and ignore the base/plinth value.
                    _add_unique_evidence(result.colours, equipment_type, Evidence(
                        field="cabinet_colour_in_ext", value=f"{ral_values[1]}/{ral_values[0]}",
                        source_file=path.name, location=f"page {page_number}",
                        extraction_method="dca_layout_label", confidence=0.98, revision=revision,
                    ))
                    layout_found = True
    if layout_found:
        result.issues = [issue for issue in result.issues if issue.code != "DCA_NO_EXPLICIT_VALUES"]
    for field_name, values in (*result.quantities.items(), *result.colours.items()):
        if len({item.value for item in values}) > 1 and not any(
            issue.code == "DCA_VALUE_CONFLICT" and issue.equipment_type == field_name.split()[0]
            for issue in result.issues
        ):
            result.issues.append(ValidationIssue(
                code="DCA_VALUE_CONFLICT", severity=IssueSeverity.BLOCKING,
                message=f"DCA evidence conflicts for {field_name}: {sorted({item.value for item in values})}.",
                equipment_type=field_name.split()[0],
            ))
    return result


def extract_costed_bom(path: Path, expected_so: str) -> BomExtraction:
    result = BomExtraction()
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        result.issues.append(ValidationIssue(
            code="BOM_PARSE_ERROR", severity=IssueSeverity.BLOCKING,
            message=f"Could not parse {path.name}: {exc}",
        ))
        return result
    try:
        for sheet in workbook.worksheets:
            header_row = None
            headers = {}
            for row_number, row in enumerate(sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 30), values_only=True), 1):
                normalized = {str(value or "").strip().casefold(): index for index, value in enumerate(row)}
                if "order number" in normalized and "component description" in normalized:
                    header_row, headers = row_number, normalized
                    break
            if header_row is None:
                continue
            required = ["order number", "component description", "component required qty"]
            if any(name not in headers for name in required):
                continue
            for row_number, row in enumerate(
                sheet.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1
            ):
                so = str(row[headers["order number"]] or "").strip().removesuffix(".0")
                if so != expected_so:
                    continue
                description = str(row[headers["component description"]] or "").strip()
                quantity = str(row[headers["component required qty"]] or "").strip()
                code = str(row[headers.get("component item number", 0)] or "").strip()
                if not description:
                    continue
                if re.search(r"\b(RCC|RDC|GDDC|OCU|PCU|SIU|SDI|server|cabinet|cable)\b|RAL\s*\d{4}", description, re.IGNORECASE):
                    result.evidence.append(Evidence(
                        field="bom_component", value=f"{code} | {description} | qty {quantity}",
                        source_file=path.name, location=f"{sheet.title}!{row_number}",
                        extraction_method="bom_row", confidence=0.80,
                        validation_state="supporting_only",
                    ))
    finally:
        workbook.close()
    if not result.evidence:
        result.issues.append(ValidationIssue(
            code="BOM_NO_RELEVANT_ROWS", severity=IssueSeverity.WARNING,
            message=f"No relevant equipment/component rows were found in {path.name}.",
        ))
    return result


def parse_drawing_pages(pages: list[str], source_file: str, expected_so: str) -> DrawingExtraction:
    result = DrawingExtraction(serial_colours={}, type_colours={})
    serial_pattern = re.compile(
        rf"\b{re.escape(expected_so)}-(?:(?:CAB-)?(?:RCC|RDC|GDDC|CBM|MCMS)(?:-?\d+)?|OCU-\d+|PCU-\d+|SUI-\d+|SIU-\d+|SDI-\d+|SER-\d+)\b",
        re.IGNORECASE,
    )
    # A bare nnnn/nnnn pattern is unsafe in a drawing title block: telephone
    # numbers have exactly that shape.  Require either an explicit RAL label or
    # a nearby colour/finish label before accepting a paired finish.
    colour_pattern = re.compile(
        r"(?:RAL\s*)?(\d{4})\s*[/,]\s*(?:RAL\s*)?(\d{4})",
        re.IGNORECASE,
    )
    for page_number, text in enumerate(pages, 1):
        if re.search(r"PDM\s*/\s*GDM\s+DISTRIBUTION\s+CABINET\s+WIRING", text, re.IGNORECASE):
            evidence = Evidence(
                field="combined_rdc_gddc_system", value="PDM/GDM DISTRIBUTION CABINET WIRING",
                source_file=source_file, location=f"page {page_number}",
                extraction_method="drawing_explicit_combined_cabinet_title", confidence=0.99,
                validation_state="verified",
            )
            if not result.combined_rdc_gddc:
                result.combined_rdc_gddc.append(evidence)
                result.evidence.append(evidence)
        serials = list(dict.fromkeys(
            _normalize_production_serial(match.group(0)) for match in serial_pattern.finditer(text)
        ))
        colours = []
        for match in colour_pattern.finditer(text):
            context = text[max(0, match.start() - 80):match.end() + 40]
            if re.search(r"RAL|COLOU?R|FINISH", context, re.IGNORECASE):
                colours.append(f"{match.group(1)}/{match.group(2)}")
        colours = list(dict.fromkeys(colours))
        # GA drawings often label internal and external finishes on separate
        # lines. Build the Smartsheet In/Ext value from those labels and do not
        # mistake the separately labelled plinth colour for a cabinet finish.
        external = re.search(
            r"(?:ALL\s*)?EXTERNAL\s*SURFACES?[^\r\n]{0,120}?RAL\s*(\d{4})",
            text, re.IGNORECASE,
        )
        internal = re.search(
            r"(?:ALL\s*(?:PAINTED\s*)?)?INTERNAL\s*SURFACES?[^\r\n]{0,120}?RAL\s*(\d{4})",
            text, re.IGNORECASE,
        )
        if external and internal:
            colours = [f"{internal.group(1)}/{external.group(1)}"]
            if re.search(r"GAS\s+DENSITY\s+DISTRIBUTION\s+CABINETS?", text, re.IGNORECASE):
                finish = Evidence(
                    field="cabinet_colour_in_ext", value=colours[0], source_file=source_file,
                    location=f"page {page_number}",
                    extraction_method="drawing_gddc_labelled_finish", confidence=0.99,
                    validation_state="verified",
                )
                result.type_colours.setdefault("GDDC", []).append(finish)
                result.evidence.append(finish)
        for serial in serials:
            result.evidence.append(Evidence(
                field="drawing_serial", value=serial, source_file=source_file,
                location=f"page {page_number}", extraction_method="drawing_explicit_serial",
                confidence=0.94, validation_state="candidate",
            ))
        if len(serials) == 1 and len(colours) == 1:
            result.serial_colours.setdefault(serials[0].casefold(), []).append(Evidence(
                field="cabinet_colour_in_ext", value=colours[0], source_file=source_file,
                location=f"page {page_number}", extraction_method="drawing_same_page_finish",
                confidence=0.92, validation_state="candidate",
            ))
        elif serials and len(colours) > 1:
            result.issues.append(ValidationIssue(
                code="DRAWING_COLOUR_AMBIGUOUS", severity=IssueSeverity.WARNING,
                message=f"{source_file} page {page_number} contains serials and multiple finishes; no mapping was assumed.",
            ))
    return result


def extract_drawing_pdf(path: Path, expected_so: str) -> DrawingExtraction:
    try:
        # PyMuPDF is substantially faster on large engineering drawing packs
        # (and is also used below to render the single OCR fallback page).
        import fitz

        document = fitz.open(str(path))
        pages = [page.get_text("text") or "" for page in document]
        result = parse_drawing_pages(pages, path.name, expected_so)
        # Some legacy GA drawings store their finish notes as vector artwork,
        # so PDF text extraction sees the cabinet title but not the RAL notes.
        # OCR only the first detail sheet following a GDDC title sheet; never
        # OCR the full drawing pack. The extraction cache prevents repeat work.
        if not result.type_colours.get("GDDC"):
            title_page = next((
                index for index, text in enumerate(pages)
                if re.search(r"GAS\s+DENSITY\s+DISTRIBUTION\s+CABINETS?", text, re.IGNORECASE)
            ), None)
            if title_page is not None and title_page + 1 < len(pages):
                try:
                    from winrt.windows.graphics.imaging import BitmapDecoder
                    from winrt.windows.media.ocr import OcrEngine
                    from winrt.windows.storage import FileAccessMode, StorageFile

                    async def read_finish_notes() -> str:
                        temp_path = Path(tempfile.gettempdir()) / (
                            f"dms-gddc-{expected_so}-{title_page + 2}.png"
                        )
                        try:
                            document = fitz.open(str(path))
                            page = document[title_page + 1]
                            page.get_pixmap(
                                matrix=fitz.Matrix(1.5, 1.5), alpha=False,
                            ).save(str(temp_path))
                            storage_file = await StorageFile.get_file_from_path_async(str(temp_path))
                            stream = await storage_file.open_async(FileAccessMode.READ)
                            try:
                                decoder = await BitmapDecoder.create_async(stream)
                                bitmap = await decoder.get_software_bitmap_async()
                                engine = OcrEngine.try_create_from_user_profile_languages()
                                if engine is None:
                                    return ""
                                recognized = await engine.recognize_async(bitmap)
                                positioned_lines = []
                                for line in recognized.lines:
                                    if line.words:
                                        rect = line.words[0].bounding_rect
                                        positioned_lines.append((rect.y, rect.x, line.text))
                                return " ".join(
                                    text for _, _, text in sorted(positioned_lines)
                                )
                            finally:
                                stream.close()
                        finally:
                            temp_path.unlink(missing_ok=True)

                    ocr_text = asyncio.run(read_finish_notes())
                    # Carry the explicit drawing identity onto the detail page;
                    # only this identified cabinet family receives the finish.
                    ocr_text = f"GAS DENSITY DISTRIBUTION CABINET {ocr_text}"
                    ocr_result = parse_drawing_pages(
                        [ocr_text], path.name, expected_so,
                    )
                    result.type_colours.update(ocr_result.type_colours)
                    result.evidence.extend(ocr_result.evidence)
                    result.issues.extend(ocr_result.issues)
                except Exception as exc:
                    result.issues.append(ValidationIssue(
                        code="DRAWING_OCR_UNAVAILABLE", severity=IssueSeverity.WARNING,
                        message=f"Could not OCR labelled GDDC finish notes in {path.name}: {exc}",
                    ))
        return result
    except Exception as exc:
        return DrawingExtraction(issues=[ValidationIssue(
            code="DRAWING_PARSE_ERROR", severity=IssueSeverity.WARNING,
            message=f"Could not parse {path.name}: {exc}",
        )])
