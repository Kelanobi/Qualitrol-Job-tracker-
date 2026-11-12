from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path
import re

from app.phase2.document_inventory import DocumentInventoryService
from app.phase2.extraction_cache import DOCUMENT_EXTRACTION_CACHE
from app.phase2.extractors import extract_costed_bom, extract_dca_pdf, extract_dcl_pdf, extract_drawing_pdf
from app.phase2.models import (
    EquipmentCandidate, IssueSeverity, LiveSheetRow, Phase2DryRun, ValidationIssue,
)
from app.phase2.project_eligibility import ProjectEligibilityService
from app.phase2.reconciliation import (
    DependencyRuleEngine, SystemConfiguration, add_missing_serial_issues,
    apply_approved_component_serials, apply_ocu_environments, generate_equipment_serials, sort_detected_ocus,
)
from app.phase2.sync_planner import SyncPlanner
from app.flow.catalogue import MaterialCatalogue
from app.flow.dcl_parser import extract_flow_dcl_pdf


class Phase2DryRunService:
    """Read-only Project pipeline. It has no Smartsheet write dependency by design."""

    def __init__(self, workbook_path: Path, document_root: Path, worksheet_name: str = "Sheet1"):
        self.eligibility = ProjectEligibilityService(workbook_path, worksheet_name)
        self.inventory = DocumentInventoryService(document_root)
        self.planner = SyncPlanner()

    def prepare(
        self,
        so: str,
        live_rows: list[LiveSheetRow] | None = None,
        processing_date: date | None = None,
        progress=None,
        order=None,
    ) -> Phase2DryRun:
        progress = progress or (lambda _percent, _message: None)
        progress(25, f"Locating current Project documents for {so}…")
        order = order or self.eligibility.find_project(so)
        documents = self.inventory.inventory(order.so)
        progress(35, f"Found {len(documents)} current documents; reading DCL…")
        issues: list[ValidationIssue] = []
        evidence_by_serial: dict[str, list] = defaultdict(list)
        customers: dict[str, str] = {}
        dca_quantities: dict[str, list] = defaultdict(list)
        dca_number_sequences: dict[str, list] = defaultdict(list)
        dca_colours: dict[str, list] = defaultdict(list)
        dca_colour_batches: dict[str, list] = defaultdict(list)
        dca_environment_batches: dict[str, list] = defaultdict(list)
        supporting_evidence = []
        drawing_colours: dict[str, list] = defaultdict(list)
        drawing_type_colours: dict[str, list] = defaultdict(list)
        combined_rdc_gddc_evidence = []
        dcl_serial_types: set[str] = set()
        flow_style_dcl_materials: list[str] = []

        for document_index, document in enumerate(documents):
            if document.detected_type != "despatch_checklist" or document.parse_status == "error":
                continue
            extraction = DOCUMENT_EXTRACTION_CACHE.extract(extract_dcl_pdf, document.path, document.revision)
            issues.extend(extraction.issues)
            extracted_serials = sum(len(values) for values in extraction.serials.values())
            if not extracted_serials:
                flow_extraction = DOCUMENT_EXTRACTION_CACHE.extract(extract_flow_dcl_pdf, document.path, order.so)
                catalogue = MaterialCatalogue()
                flow_style_dcl_materials.extend(
                    line.part_code or line.description
                    for line in flow_extraction.lines if catalogue.match(line)
                )
            documents[document_index] = replace(
                document,
                parse_status="parsed" if extracted_serials else "needs review",
                error=None if extracted_serials else "No supported Project equipment serials found in this DCL.",
            )
            for evidence in extraction.commercial_customers:
                customers.setdefault(evidence.value.casefold(), evidence.value)
            for serial_evidence in extraction.serials.values():
                for evidence in serial_evidence:
                    if not evidence.value.casefold().startswith(f"{order.so}-".casefold()):
                        issues.append(ValidationIssue(
                            code="CROSS_ORDER_SERIAL", severity=IssueSeverity.BLOCKING,
                            message=(
                                f"Serial {evidence.value} in {evidence.source_file} does not belong "
                                f"to selected order {order.so}; it was excluded."
                            ),
                            serial=evidence.value,
                        ))
                        continue
                    evidence_by_serial[evidence.value.casefold()].append(evidence)
                    serial_tail = evidence.value.split("-", 1)[1].upper()
                    dcl_serial_types.add(
                        serial_tail.split("-", 2)[1]
                        if serial_tail.startswith("CAB-") else serial_tail.split("-", 1)[0]
                    )

        # Pick the highest explicit revision independently for PDM and GDM.
        progress(48, "Reading PDM/GDM specifications…")
        dca_documents = [
            document for document in documents
            if document.detected_type in {"pdm_dca", "gdm_dca", "dca"}
            and document.extension == ".pdf" and document.parse_status != "error"
        ]
        selected_dcas = []
        for family in {document.detected_type for document in dca_documents}:
            family_documents = [item for item in dca_documents if item.detected_type == family]
            revisions = [item.revision for item in family_documents if item.revision]
            if revisions:
                highest = max(revisions, key=lambda value: (len(value), value.casefold()))
                family_documents = [item for item in family_documents if item.revision == highest]
            selected_dcas.extend(family_documents)
        for document in selected_dcas:
            extraction = DOCUMENT_EXTRACTION_CACHE.extract(
                extract_dca_pdf, document.path, document.detected_type, document.revision,
            )
            issues.extend(extraction.issues)
            for equipment_type, values in extraction.quantities.items():
                dca_quantities[equipment_type].extend(values)
            for equipment_type, values in extraction.number_sequences.items():
                dca_number_sequences[equipment_type].extend(values)
            for equipment_type, values in extraction.colours.items():
                dca_colours[equipment_type].extend(values)
            for equipment_type, values in extraction.colour_batches.items():
                dca_colour_batches[equipment_type].extend(values)
            for equipment_type, values in extraction.environment_batches.items():
                dca_environment_batches[equipment_type].extend(values)

        progress(60, "Reconciling the costed BOM…")
        for document in documents:
            if document.detected_type == "costed_bom" and document.parse_status != "error":
                extraction = DOCUMENT_EXTRACTION_CACHE.extract(extract_costed_bom, document.path, order.so)
                supporting_evidence.extend(extraction.evidence)
                issues.extend(extraction.issues)

        progress(68, "Reading current technical drawings…")
        drawing_documents = [
            item for item in documents
            if item.detected_type == "technical_drawing" and item.parse_status != "error"
        ]
        drawing_families: dict[str, list] = defaultdict(list)
        for document in drawing_documents:
            family = document.filename.casefold()
            family = re.sub(r"\s+rev(?:ision)?[\s._-]*[a-z0-9]+", "", family)
            drawing_families[family].append(document)
        selected_drawings = []
        for family_documents in drawing_families.values():
            revisions = [item.revision for item in family_documents if item.revision]
            candidates = family_documents
            if revisions:
                highest = max(revisions, key=lambda value: (len(value), value.casefold()))
                candidates = [item for item in candidates if item.revision == highest]
            selected_drawings.append(min(candidates, key=lambda item: (len(item.path.parts), str(item.path).casefold())))
        # Drawing extraction is independent per file.  Running the small active
        # drawing set concurrently avoids serially waiting on several network PDF
        # reads while preserving the selected-document order in the results.
        with ThreadPoolExecutor(max_workers=min(4, len(selected_drawings) or 1)) as executor:
            drawing_extractions = executor.map(
                lambda document: DOCUMENT_EXTRACTION_CACHE.extract(
                    extract_drawing_pdf, document.path, order.so,
                ),
                selected_drawings,
            )
        for extraction in drawing_extractions:
            supporting_evidence.extend(extraction.evidence)
            issues.extend(extraction.issues)
            combined_rdc_gddc_evidence.extend(extraction.combined_rdc_gddc)
            for serial, values in extraction.serial_colours.items():
                drawing_colours[serial].extend(values)
            for equipment_type, values in extraction.type_colours.items():
                drawing_type_colours[equipment_type].extend(values)

        commercial_customer = next(iter(customers.values()), None) if len(customers) == 1 else None
        progress(82, "Reconciling equipment quantities, serials and colours…")
        if len(customers) > 1:
            issues.append(ValidationIssue(
                code="COMMERCIAL_CUSTOMER_CONFLICT", severity=IssueSeverity.BLOCKING,
                message=f"DCL files disagree on commercial customer: {sorted(customers.values())}",
            ))

        equipment: list[EquipmentCandidate] = []
        counts = defaultdict(int)
        for grouped_evidence in evidence_by_serial.values():
            serial = grouped_evidence[0].value
            tail = serial.split("-", 1)[1].upper()
            cabinet_tail = tail[4:] if tail.startswith("CAB-") else tail
            numbered_cabinet = re.fullmatch(r"(RCC|RDC|GDDC|CBM|MCMS)-?\d+", cabinet_tail)
            equipment_type = (
                tail[:3] if tail.startswith(("OCU-", "ICU-"))
                else numbered_cabinet.group(1) if numbered_cabinet
                else tail
            )
            counts[equipment_type] += 1
            equipment.append(EquipmentCandidate(
                equipment_type=equipment_type,
                serial=serial,
                serial_source="DCL",
                evidence=grouped_evidence,
            ))

        has_project_support = any(
            document.detected_type in {"pdm_dca", "gdm_dca", "dca", "costed_bom", "technical_drawing"}
            for document in documents
        )
        if flow_style_dcl_materials and not equipment and not has_project_support:
            issues.append(ValidationIssue(
                code="WORKBOOK_CLASSIFICATION_MISMATCH",
                severity=IssueSeverity.BLOCKING,
                message=(
                    f"Excel classifies order {order.so} as Project, but only a Flow-style DCL material list "
                    f"was found ({', '.join(sorted(set(flow_style_dcl_materials)))}). If this is a Flow job, "
                    "change Column A for this order to 'Flow', save the workbook, then refresh and retry Phase 2."
                ),
            ))

        serial_prefixes = {
            "RCC": "CAB-RCC", "RDC": "CAB-RDC", "GDDC": "CAB-GDDC",
            "CBM": "CAB-CBM", "MCMS": "CAB-MCMS", "SIU": "SIU",
        }
        for equipment_type, sequence_evidence in dca_number_sequences.items():
            sequences = {
                tuple(int(number) for number in item.value.split(",") if number)
                for item in sequence_evidence
            }
            if len(sequences) > 1:
                issues.append(ValidationIssue(
                    code="DCA_NUMBER_SEQUENCE_CONFLICT", severity=IssueSeverity.BLOCKING,
                    message=f"Selected DCA documents disagree on {equipment_type} numbers: {sorted(sequences)}.",
                    equipment_type=equipment_type,
                ))
                continue
            prefix = serial_prefixes.get(equipment_type, equipment_type)
            existing_numbers = {
                int(match.group(1))
                for item in equipment if item.equipment_type == equipment_type and item.serial
                for match in [re.search(rf"-{re.escape(prefix)}-(\d+)$", item.serial, re.IGNORECASE)]
                if match
            }
            for number in next(iter(sequences), ()):
                if number in existing_numbers:
                    continue
                equipment.append(EquipmentCandidate(
                    equipment_type=equipment_type,
                    serial=f"{order.so}-{prefix}-{number:02d}",
                    serial_source="explicit DCA equipment numbering",
                    evidence=list(sequence_evidence),
                ))

        for equipment_type, evidence_values in dca_quantities.items():
            distinct = {int(item.value) for item in evidence_values}
            if len(distinct) > 1:
                issues.append(ValidationIssue(
                    code="DCA_QUANTITY_CONFLICT", severity=IssueSeverity.BLOCKING,
                    message=f"Selected DCA documents disagree on {equipment_type} quantity: {sorted(distinct)}.",
                    equipment_type=equipment_type,
                ))
                continue
            required = next(iter(distinct))
            present = sum(item.equipment_type == equipment_type for item in equipment)
            if required < present:
                issues.append(ValidationIssue(
                    code="DCL_DCA_QUANTITY_CONFLICT", severity=IssueSeverity.BLOCKING,
                    message=f"DCL has {present} {equipment_type} serials but DCA quantity is {required}.",
                    equipment_type=equipment_type,
                ))
            missing = max(0, required - present)
            explicit_serials = [
                item.serial for item in equipment
                if item.equipment_type == equipment_type and item.serial
            ]
            generated = []
            if missing and not explicit_serials and equipment_type not in dca_number_sequences:
                generated = generate_equipment_serials(order.so, equipment_type, required)
            for index in range(missing):
                equipment.append(EquipmentCandidate(
                    equipment_type=equipment_type,
                    serial=generated[index] if index < len(generated) else None,
                    serial_source=(
                        "approved quantity-based equipment numbering"
                        if index < len(generated) else "DCA quantity"
                    ),
                    evidence=list(evidence_values),
                ))

        # A combined PDM/GDM distribution cabinet is physically one cabinet.
        # Apply this only when the drawing explicitly says so and the DCL did not
        # independently identify a GDDC/GDM cabinet.
        if combined_rdc_gddc_evidence and "GDDC" not in dcl_serial_types:
            rdcs = [item for item in equipment if item.equipment_type == "RDC"]
            gddcs = [item for item in equipment if item.equipment_type == "GDDC"]
            pair_count = min(len(rdcs), len(gddcs))
            if pair_count:
                paired_ids = {id(item) for item in rdcs[:pair_count] + gddcs[:pair_count]}
                equipment = [item for item in equipment if id(item) not in paired_ids]
                for index in range(pair_count):
                    equipment.append(EquipmentCandidate(
                        equipment_type="RDC/GDDC",
                        serial=f"{order.so}-CAB-RDC-GDDC-{index + 1:02d}",
                        serial_source="combined PDM/GDM distribution cabinet drawing",
                        evidence=(
                            list(rdcs[index].evidence) + list(gddcs[index].evidence)
                            + list(combined_rdc_gddc_evidence)
                        ),
                    ))
                issues.append(ValidationIssue(
                    code="COMBINED_RDC_GDDC_CONFIRMED", severity=IssueSeverity.INFO,
                    message=(
                        f"Drawing evidence confirms {pair_count} combined PDM/GDM distribution cabinet(s); "
                        f"using {order.so}-CAB-RDC-GDDC-01 naming."
                    ),
                    equipment_type="RDC/GDDC",
                ))

        # Apply mixed OCU colours in table order only when every quantity batch
        # reconciles exactly with the explicit DCL OCU serial population.
        # Otherwise leave OCU colour blank rather than assigning a potentially
        # wrong finish to a DCA-generated or unmatched candidate.
        ocu_candidates = [
            item for item in equipment
            if item.equipment_type == "OCU" and item.serial_source == "DCL"
        ]
        ocu_batches = dca_colour_batches.get("OCU", [])
        if ocu_batches:
            expanded_colours = [
                (colour, evidence)
                for quantity, colour, evidence in ocu_batches
                for _index in range(quantity)
            ]
            if len(expanded_colours) == len(ocu_candidates):
                def ocu_sort_key(item):
                    match = re.search(r"-OCU-(\d+)$", item.serial or "", re.IGNORECASE)
                    return int(match.group(1)) if match else 10**9

                for candidate, (colour, evidence) in zip(sorted(ocu_candidates, key=ocu_sort_key), expanded_colours):
                    candidate.colour = colour
                    candidate.evidence.append(evidence)

        # The Specs 4.3 table is authoritative for OCU installation location.
        # Indoor rows are ICU; outdoor rows remain OCU. Apply only when its
        # quantity batches exactly reconcile with the explicit DCL population.
        apply_ocu_environments(equipment, dca_environment_batches.get("OCU", []))

        for candidate in equipment:
            colour_evidence = dca_colours.get(
                "OCU" if candidate.equipment_type == "ICU" else candidate.equipment_type, []
            )
            if candidate.equipment_type == "RDC/GDDC":
                colour_evidence = list(dca_colours.get("RDC", [])) + list(dca_colours.get("GDDC", []))
            distinct_colours = {item.value for item in colour_evidence}
            if (
                len(distinct_colours) == 1
                and candidate.equipment_type not in {"PCU", "SIU", "SDI", "SER"}
                and not candidate.colour
            ):
                candidate.colour = next(iter(distinct_colours))
                candidate.evidence.extend(colour_evidence)
            elif len(distinct_colours) > 1:
                candidate.issues.append(ValidationIssue(
                    code="DCA_COLOUR_CONFLICT", severity=IssueSeverity.BLOCKING,
                    message=f"Conflicting {candidate.equipment_type} finishes: {sorted(distinct_colours)}.",
                    equipment_type=candidate.equipment_type, serial=candidate.serial,
                ))
            # Specs remain authoritative for OCU/RCC/RDC. For GDDC, an
            # explicitly labelled GA drawing finish is more specific and may
            # correct a generic or misclassified Specs value. A type-level GA
            # note applies to every GDDC in that identified drawing set.
            if candidate.equipment_type == "GDDC":
                explicit_drawing_colours = (
                    drawing_colours.get((candidate.serial or "").casefold(), [])
                    + drawing_type_colours.get("GDDC", [])
                )
                drawing_values = {item.value for item in explicit_drawing_colours}
                if len(drawing_values) == 1:
                    candidate.colour = next(iter(drawing_values))
                    candidate.evidence.extend(explicit_drawing_colours)

        reconciled_counts = defaultdict(int)
        for candidate in equipment:
            reconciled_counts[candidate.equipment_type] += 1
        derived, dependency_issues = DependencyRuleEngine().derive(SystemConfiguration(
            rcc=reconciled_counts["RCC"], rdc=reconciled_counts["RDC"],
            gddc=reconciled_counts["GDDC"], ocu=reconciled_counts["OCU"] + reconciled_counts["ICU"],
        ))
        equipment.extend(derived)
        issues.extend(dependency_issues)
        # A combined PDM/GDM distribution cabinet always uses exactly one sync
        # driver. Preserve an explicit DCL SDI when present; otherwise derive it.
        if any(item.equipment_type == "RDC/GDDC" for item in equipment):
            sync_drivers = [item for item in equipment if item.equipment_type == "SDI"]
            if not sync_drivers:
                equipment.append(EquipmentCandidate(
                    equipment_type="SDI",
                    serial_source="combined RDC/GDDC mandatory sync driver rule",
                    evidence=list(combined_rdc_gddc_evidence),
                ))
            elif len(sync_drivers) > 1:
                keep = sync_drivers[0]
                equipment = [
                    item for item in equipment
                    if item.equipment_type != "SDI" or item is keep
                ]
        apply_approved_component_serials(order.so, equipment)
        sort_detected_ocus(equipment)
        add_missing_serial_issues(equipment)

        if not dca_documents:
            issues.append(ValidationIssue(
                code="NO_DCA_DOCUMENT", severity=IssueSeverity.WARNING,
                message="No DCA PDF was found; subsystem equipment may be incomplete.",
            ))
        if live_rows is None:
            issues.append(ValidationIssue(
                code="SMARTSHEET_OFFLINE_PREVIEW", severity=IssueSeverity.INFO,
                message="No live Smartsheet snapshot was supplied; proposed creates have not been duplicate-checked online.",
            ))

        plan_rows = self.planner.plan(
            order=order,
            equipment=equipment,
            live_rows=live_rows or [],
            commercial_customer=commercial_customer,
            processing_date=processing_date or date.today(),
        )
        progress(95, "Building the editable Smartsheet preview…")
        return Phase2DryRun(
            order=order, documents=documents, equipment=equipment, issues=issues,
            plan_rows=plan_rows, evidence=supporting_evidence,
        )
