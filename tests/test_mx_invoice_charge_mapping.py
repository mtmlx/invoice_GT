from pathlib import Path

from clickup_integration.invoice_sync import load_invoice_charge_mappings


def test_mx_ocean_freight_uses_the_live_bc_item_description() -> None:
    mapping_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "invoice_charge_mappings"
        / "mx.json"
    )

    mappings = {
        mapping.charge_name: mapping
        for mapping in load_invoice_charge_mappings(mapping_path)
    }

    freight = mappings["Freight (Ocean/Truck/Air)"]
    assert freight.bc_item_number == "INT000000026"
    assert freight.bc_description == "TRANSPORTE MARITIMO"


def test_mx_destination_customs_uses_national_item_separate_from_origin() -> None:
    mappings = {
        mapping.clickup_field_id: mapping
        for mapping in load_invoice_charge_mappings(
            Path(__file__).resolve().parents[1] / "config/invoice_charge_mappings/mx.json"
        )
    }
    destination = mappings["c88a27b6-6325-44bb-983a-3948017061b4"]
    assert destination.bc_item_number == "NAT00000030"
    assert destination.bc_description == "Coordinacion de agente aduanal"
    assert mappings["fd301630-ec8b-4b30-b011-41412aa02576"].bc_item_number == "INT000000028"
