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
