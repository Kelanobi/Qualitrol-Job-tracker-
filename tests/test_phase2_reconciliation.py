import pytest

from app.phase2.models import IssueSeverity
from app.phase2.models import EquipmentCandidate
from app.phase2.reconciliation import (
    DependencyRuleEngine, SystemConfiguration, apply_approved_component_serials, apply_ocu_environments,
    generate_equipment_serials, generate_ocu_serials,
    sort_detected_ocus,
)
from app.phase2.models import Evidence


def types_for(configuration):
    equipment, issues = DependencyRuleEngine().derive(configuration)
    return [item.equipment_type for item in equipment], issues


def test_ocu_serials_use_minimum_two_digits_and_detect_collisions():
    assert generate_ocu_serials("50577444", 3) == [
        "50577444-OCU-01", "50577444-OCU-02", "50577444-OCU-03"
    ]
    with pytest.raises(ValueError, match="collides"):
        generate_ocu_serials("50577444", 2, {"50577444-ocu-02"})


def test_specs_environment_changes_indoor_ocus_to_icu_and_preserves_numbers():
    evidence = Evidence("ocu_environment_batch", "Indoor", "spec.pdf", "page 1", "layout", 0.99)
    equipment = [
        EquipmentCandidate("OCU", serial=f"50577444-OCU-{number:02d}", serial_source="DCL")
        for number in (1, 3, 7)
    ]
    assert apply_ocu_environments(equipment, [(2, "Indoor", evidence), (1, "Outdoor", evidence)])
    assert [(item.equipment_type, item.serial) for item in equipment] == [
        ("ICU", "50577444-ICU-01"),
        ("ICU", "50577444-ICU-03"),
        ("OCU", "50577444-OCU-07"),
    ]


def test_specs_environment_is_not_applied_when_quantity_does_not_match_dcl():
    evidence = Evidence("ocu_environment_batch", "Indoor", "spec.pdf", "page 1", "layout", 0.99)
    equipment = [EquipmentCandidate("OCU", serial="50577444-OCU-01", serial_source="DCL")]
    assert not apply_ocu_environments(equipment, [(2, "Indoor", evidence)])
    assert (equipment[0].equipment_type, equipment[0].serial) == ("OCU", "50577444-OCU-01")


def test_quantity_based_cabinet_serials_use_approved_format():
    assert generate_equipment_serials("50555486", "RCC", 1) == ["50555486-CAB-RCC-01"]
    assert generate_equipment_serials("50555486", "RDC", 3) == [
        "50555486-CAB-RDC-01", "50555486-CAB-RDC-02", "50555486-CAB-RDC-03",
    ]


def test_dependency_rules_cover_agreed_combinations():
    types, issues = types_for(SystemConfiguration(rcc=1, rdc=0, gddc=0, ocu=2))
    assert types == ["PCU", "SER", "SIU"]
    assert not issues

    types, issues = types_for(SystemConfiguration(rcc=1, rdc=0, gddc=1, ocu=0))
    assert types == ["PCU", "SER", "SIU"]
    assert not issues


def test_rcc_rdc_gddc_dependencies_are_applied_independently():
    types, issues = types_for(SystemConfiguration(rcc=1, rdc=1, gddc=1, ocu=11))
    assert types == ["PCU", "SER", "SIU", "SDI"]
    assert not issues


def test_rcc_alone_gets_pcu_server_and_sync_interface():
    types, issues = types_for(SystemConfiguration(rcc=1, rdc=0, gddc=0, ocu=0))
    assert types == ["PCU", "SER", "SIU"]
    assert not issues


def test_approved_component_formats_include_siu_spelling_and_server_per_rcc():
    equipment, _ = DependencyRuleEngine().derive(SystemConfiguration(rcc=1, rdc=0, gddc=0, ocu=2))
    apply_approved_component_serials("50577574", equipment)
    assert [(item.equipment_type, item.serial) for item in equipment] == [
        ("PCU", "50577574-PCU-01"),
        ("SER", "50577574-SER-01"),
        ("SIU", "50577574-SIU-01"),
    ]


def test_each_rcc_and_rdc_gets_its_own_interface_or_driver():
    equipment, issues = DependencyRuleEngine().derive(
        SystemConfiguration(rcc=2, rdc=3, gddc=0, ocu=0)
    )
    assert not issues
    assert [item.equipment_type for item in equipment].count("PCU") == 2
    assert [item.equipment_type for item in equipment].count("SER") == 2
    assert [item.equipment_type for item in equipment].count("SIU") == 2
    assert [item.equipment_type for item in equipment].count("SDI") == 3


def test_document_server_quantity_can_override_one_per_rcc_default():
    equipment, _ = DependencyRuleEngine().derive(
        SystemConfiguration(rcc=2, rdc=0, gddc=2, ocu=0, server_quantity=1)
    )
    assert [item.equipment_type for item in equipment].count("SER") == 1


def test_explicit_component_serial_is_never_rewritten():
    equipment = [EquipmentCandidate("PCU", serial="CUSTOM-PCU")]
    apply_approved_component_serials("50577574", equipment)
    assert equipment[0].serial == "CUSTOM-PCU"


def test_detected_ocus_are_sorted_numerically_without_inventing_missing_numbers():
    equipment = [
        EquipmentCandidate("RCC", serial="50577444-RCC-01"),
        EquipmentCandidate("OCU", serial="50577444-OCU-11"),
        EquipmentCandidate("OCU", serial="50577444-OCU-03"),
        EquipmentCandidate("OCU", serial="50577444-OCU-01"),
        EquipmentCandidate("OCU", serial="50577444-OCU-05"),
        EquipmentCandidate("PCU", serial="50577444-PCU-01"),
    ]

    sort_detected_ocus(equipment)

    assert [item.serial for item in equipment] == [
        "50577444-RCC-01",
        "50577444-OCU-01",
        "50577444-OCU-03",
        "50577444-OCU-05",
        "50577444-OCU-11",
        "50577444-PCU-01",
    ]
