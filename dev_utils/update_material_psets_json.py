import json
import ifcopenshell.util.pset

def update_primary_measure_type(psets):
    pset_qto = ifcopenshell.util.pset.PsetQto("IFC4")
    for pset_name, props in psets.items():
        pset = pset_qto.get_by_name(pset_name)
        if pset:
            for prop in pset.HasPropertyTemplates:
                psets[pset_name][prop.Name]["primary_measure_type"] = prop.PrimaryMeasureType
        else:
            for values in props.values():
                values["primary_measure_type"] = None

with open("material_psets.json", "r") as f:
    PSETS = json.load(f)
    update_primary_measure_type(PSETS)
with open("material_psets.json", "w") as f:
    json.dump(PSETS, f)
