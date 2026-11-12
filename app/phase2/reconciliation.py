from __future__ import annotations

from dataclasses import dataclass
import re

from app.phase2.models import EquipmentCandidate, IssueSeverity, ValidationIssue


CABINET_TYPES = {"RCC", "RDC", "GDDC", "CBM", "MCMS"}
TRACKED_TYPES = CABINET_TYPES | {"OCU", "ICU", "PCU", "SIU", "SDI", "SER"}
APPROVED_COMPONENT_SERIAL_PREFIXES = {
    "RCC": "CAB-RCC",
    "RDC": "CAB-RDC",
    "GDDC": "CAB-GDDC",
    "CBM": "CAB-CBM",
    "MCMS": "CAB-MCMS",
    "PCU": "PCU",
    "SIU": "SIU",
    "SDI": "SDI",
    "SER": "SER",
}


def sort_detected_ocus(equipment: list[EquipmentCandidate]) -> None:
    """Sort only OCU candidates numerically, retaining the surrounding row order."""
    positions = [index for index, item in enumerate(equipment) if item.equipment_type in {"OCU", "ICU"}]

    def serial_key(item: EquipmentCandidate) -> tuple[int, str]:
        serial = (item.serial or "").strip()
        match = re.search(r"-(?:OCU|ICU)-(\d+)$", serial, re.IGNORECASE)
        return (int(match.group(1)) if match else 10**12, serial.casefold())

    ordered = sorted((equipment[index] for index in positions), key=serial_key)
    for index, candidate in zip(positions, ordered):
        equipment[index] = candidate


def apply_ocu_environments(
    equipment: list[EquipmentCandidate], batches: list[tuple[int, str, object]],
) -> bool:
    """Apply Specs Indoor/Outdoor rows only when they exactly cover DCL OCUs."""
    candidates = [
        item for item in equipment
        if item.equipment_type in {"OCU", "ICU"} and item.serial_source == "DCL"
    ]
    expanded = [
        (environment, evidence)
        for quantity, environment, evidence in batches
        for _index in range(quantity)
    ]
    if not candidates or len(expanded) != len(candidates):
        return False

    def serial_key(item: EquipmentCandidate) -> tuple[int, str]:
        serial = item.serial or ""
        match = re.search(r"-(?:OCU|ICU)-(\d+)$", serial, re.IGNORECASE)
        return (int(match.group(1)) if match else 10**12, serial.casefold())

    for candidate, (environment, evidence) in zip(sorted(candidates, key=serial_key), expanded):
        indoor = str(environment).casefold() == "indoor"
        candidate.equipment_type = "ICU" if indoor else "OCU"
        if candidate.serial:
            candidate.serial = re.sub(
                r"-(?:OCU|ICU)-(?=\d+$)", "-ICU-" if indoor else "-OCU-",
                candidate.serial, flags=re.IGNORECASE,
            )
        candidate.evidence.append(evidence)
    return True


def generate_ocu_serials(so: str, quantity: int, occupied: set[str] | None = None) -> list[str]:
    occupied_normalized = {value.strip().casefold() for value in (occupied or set())}
    serials: list[str] = []
    for index in range(1, quantity + 1):
        serial = f"{so}-OCU-{index:02d}"
        if serial.casefold() in occupied_normalized:
            raise ValueError(f"Generated OCU serial collides with an existing serial: {serial}")
        serials.append(serial)
    return serials


def generate_equipment_serials(
    so: str, equipment_type: str, quantity: int, occupied: set[str] | None = None,
) -> list[str]:
    """Generate 01..N only when documents provide quantity and no numbering.

    Callers must not use this to fill gaps in an explicit numbering sequence.
    """
    if quantity < 0:
        raise ValueError("Equipment quantity cannot be negative.")
    prefix = APPROVED_COMPONENT_SERIAL_PREFIXES.get(equipment_type, equipment_type)
    occupied_normalized = {value.strip().casefold() for value in (occupied or set())}
    serials = [f"{so}-{prefix}-{index:02d}" for index in range(1, quantity + 1)]
    collision = next((serial for serial in serials if serial.casefold() in occupied_normalized), None)
    if collision:
        raise ValueError(f"Generated equipment serial collides with an existing serial: {collision}")
    return serials


@dataclass(frozen=True)
class SystemConfiguration:
    rcc: int
    rdc: int
    gddc: int
    ocu: int
    server_quantity: int | None = None


class DependencyRuleEngine:
    def derive(self, configuration: SystemConfiguration) -> tuple[list[EquipmentCandidate], list[ValidationIssue]]:
        equipment: list[EquipmentCandidate] = []
        issues: list[ValidationIssue] = []
        if configuration.rcc < 0 or configuration.rdc < 0 or configuration.gddc < 0 or configuration.ocu < 0:
            raise ValueError("Equipment quantities cannot be negative.")

        equipment.extend(EquipmentCandidate("PCU") for _ in range(configuration.rcc))
        server_quantity = configuration.rcc if configuration.server_quantity is None else configuration.server_quantity
        if server_quantity < 0:
            raise ValueError("Server quantity cannot be negative.")
        equipment.extend(EquipmentCandidate("SER") for _ in range(server_quantity))
        # Approved dependency rules are independent of the surrounding system
        # combination: each RCC has one sync interface, and each RDC has one
        # sync driver. This avoids losing components when quantities differ.
        equipment.extend(EquipmentCandidate("SIU") for _ in range(configuration.rcc))
        equipment.extend(EquipmentCandidate("SDI") for _ in range(configuration.rdc))
        return equipment, issues


def add_missing_serial_issues(equipment: list[EquipmentCandidate]) -> None:
    for candidate in equipment:
        if not candidate.serial and candidate.equipment_type != "OCU":
            candidate.issues.append(ValidationIssue(
                code="MISSING_APPROVED_SERIAL",
                severity=IssueSeverity.BLOCKING,
                message=f"{candidate.equipment_type} has no verified serial or approved generation policy.",
                equipment_type=candidate.equipment_type,
            ))


def apply_approved_component_serials(so: str, equipment: list[EquipmentCandidate]) -> None:
    """Apply the user-approved deterministic formats without rewriting explicit serials."""
    occupied = {item.serial.casefold() for item in equipment if item.serial}
    counters: dict[str, int] = {kind: 0 for kind in APPROVED_COMPONENT_SERIAL_PREFIXES}
    for candidate in equipment:
        prefix = APPROVED_COMPONENT_SERIAL_PREFIXES.get(candidate.equipment_type)
        if not prefix or candidate.serial:
            continue
        counters[candidate.equipment_type] += 1
        serial = f"{so}-{prefix}-{counters[candidate.equipment_type]:02d}"
        if serial.casefold() in occupied:
            candidate.issues.append(ValidationIssue(
                code="GENERATED_SERIAL_COLLISION", severity=IssueSeverity.BLOCKING,
                message=f"Approved generated serial collides with another candidate: {serial}.",
                equipment_type=candidate.equipment_type, serial=serial,
            ))
            continue
        candidate.serial = serial
        candidate.serial_source = "approved component naming policy"
        occupied.add(serial.casefold())

