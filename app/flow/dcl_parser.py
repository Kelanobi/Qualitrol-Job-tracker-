from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from pypdf import PdfReader

from app.flow.models import FlowDclLine


ORDER_RE = re.compile(r"\bOrder\s*:\s*(\d+)", re.IGNORECASE)
CUSTOMER_RE = re.compile(r"\bCustomer\s*:\s*(.+?)(?=\s+Contracts?\s*:|\s+Project\s+Substation|$)", re.IGNORECASE)
# Flow DCLs contain many valid company part families (AE, CEH, CEL, CFL,
# CML, and others). Capture the printed part number generically instead of
# silently dropping an unfamiliar prefix.
PART_RE = re.compile(r"\b[A-Z]{2,5}\d{5}(?:-\d+)?\b", re.IGNORECASE)
SERIAL_RE = re.compile(r"\b\d{6,}-(?:[A-Z0-9]+(?:-[A-Z0-9]+)*)\b", re.IGNORECASE)
LINE_REF_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)+)\s+")
QUANTITY_RE = re.compile(r"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*(m|metres?|meters?|ea|each|pcs?|pieces?)?\s*$", re.IGNORECASE)


@dataclass
class FlowDclExtraction:
    lines: list[FlowDclLine] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    orders: set[str] = field(default_factory=set)
    customers: set[str] = field(default_factory=set)


def parse_flow_dcl_pages(pages: list[str], source_file: str, selected_so: str) -> FlowDclExtraction:
    result = FlowDclExtraction()
    header_customer = None
    for text in pages:
        result.orders.update(match.group(1) for match in ORDER_RE.finditer(text))
        customer = CUSTOMER_RE.search(text)
        if customer:
            header_customer = customer.group(1).strip()
            result.customers.add(header_customer)
    if result.orders and result.orders != {selected_so}:
        result.issues.append(f"DCL order references {sorted(result.orders)} do not match selected SO {selected_so}.")

    for page_number, text in enumerate(pages, 1):
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line or not (PART_RE.search(line) or re.search(r"\b(?:OCU|OPTICAL CONVERTER UNIT|SERVER|UPS|COUPLER|CABLE)\b", line, re.IGNORECASE)):
                continue
            quantity_match = QUANTITY_RE.search(line)
            quantity = None
            unit = None
            if quantity_match:
                try:
                    quantity = Decimal(quantity_match.group(1))
                except InvalidOperation:
                    quantity = None
                unit = quantity_match.group(2) or "Each"
            part = PART_RE.search(line)
            part_code = part.group(0).upper() if part else None
            serials = [match.group(0) for match in SERIAL_RE.finditer(line)]
            # Exclude the selected SO itself and retain production-style values.
            serials = [value for value in serials if value != selected_so and value.startswith(f"{selected_so}-")]
            line_ref_match = LINE_REF_RE.match(line)
            description_end = part.start() if part else (quantity_match.start() if quantity_match else len(line))
            description = line[:description_end]
            if line_ref_match:
                description = description[line_ref_match.end():]
            for serial in serials:
                description = description.replace(serial, "")
            description = " ".join(description.split()).strip(" -,")
            result.lines.append(FlowDclLine(
                so=selected_so, customer=header_customer, source_file=source_file,
                page=page_number, line_reference=line_ref_match.group(1) if line_ref_match else None,
                part_code=part_code, description=description or line, quantity=quantity,
                unit=unit, explicit_serials=serials, raw_text=line,
            ))
    if not result.lines:
        result.issues.append(f"No Flow material lines could be extracted from {source_file}.")
    return result


def extract_flow_dcl_pdf(path: Path, selected_so: str) -> FlowDclExtraction:
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        return FlowDclExtraction(issues=[f"Could not parse {path.name}: {exc}"])
    if pages and not any(page.strip() for page in pages):
        return FlowDclExtraction(issues=[f"{path.name} is image-only or has no extractable text."])
    return parse_flow_dcl_pages(pages, path.name, selected_so)
