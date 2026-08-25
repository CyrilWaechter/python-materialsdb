import pytest

pytest.importorskip("ifcopenshell")

from materialsdb.ifc.project_library import ProjectLibrary


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


def _entity_inventory(file):
    inventory = {}
    for entity in file:
        inventory[entity.is_a()] = inventory.get(entity.is_a(), 0) + 1
    return inventory


def test_batch_export_inventory_is_stable(mini_source):
    library = ProjectLibrary()
    library.create_project_library(mini_source)
    library.create_materials(mini_source)

    print(_entity_inventory(library.file))  # keep while pinning values

    # Pinned golden snapshot of current batch-export behavior. This is a
    # characterization test: it records what the code does today, not what it
    # should do. Update values only when intentional behavior changes.
    inventory = _entity_inventory(library.file)
    expected_inventory = {
        "IfcAddress": 1,
        "IfcOrganization": 2,  # developer org (application) + manufacturer org
        "IfcApplication": 1,
        "IfcActorRole": 1,
        "IfcPerson": 1,
        "IfcPersonAndOrganization": 1,
        "IfcOwnerHistory": 3,  # 1 explicit + 2 added by assign_material for walls
        "IfcProjectLibrary": 1,
        "IfcRepresentationContext": 1,
        "IfcColourRgb": 3,  # one per material (explicit xml color or category color)
        "IfcSurfaceStyleShading": 3,
        "IfcSurfaceStyle": 3,
        "IfcStyledItem": 3,
        "IfcStyledRepresentation": 3,
        "IfcMaterial": 3,  # one per layer across fixture materials (2 + 1 + 0)
        "IfcPropertySingleValue": 6,
        "IfcMaterialProperties": 6,
        "IfcMaterialLayer": 3,
        "IfcMaterialLayerSet": 3,
        "IfcWallType": 2,  # mat1 declares wall="1" -> one per its 2 layers
        "IfcRelAssociatesMaterial": 2,
    }
    assert inventory == expected_inventory
    material_names = sorted(m.Name for m in library.file.by_type("IfcMaterial"))
    assert material_names == ["Beton B", "Isolant A", "Isolant A"]
