import json
from ifcopenshell.util.pset import PsetQto

psetqto = PsetQto("IFC4")

psets_dict = {}
for pset in psetqto.get_applicable("IfcMaterial"):
    psets_dict[pset.Name] = {p.Name: "" for p in pset.HasPropertyTemplates}

with open("material_psets.json", "w") as f:
    json.dump(psets_dict, f)
