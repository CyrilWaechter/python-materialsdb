from materialsdb.summary import summarize_material


def test_insulation_material_summary_ch(mini_source):
    material = mini_source.material[0]
    s = summarize_material(material, country="CH")

    assert s.id == "00000000-0000-0000-0000-000000000001"
    assert s.category == "Insulation"
    assert s.names == {"fr": "Isolant A", "de": "Daemmstoff A", "": "Material A"}
    assert s.descriptions == {"fr": "Panneau isolant", "de": "Daemmplatte"}
    assert s.usage["wall"] is True
    assert s.usage["roof"] is False
    assert s.thick_min == 100
    assert s.thick_max == 200
    assert s.lambda_min == 0.036
    assert s.lambda_max == 0.05


def test_french_country_picks_fr_values(mini_source):
    s = summarize_material(mini_source.material[0], country="FR")
    assert s.lambda_min == 0.04
    assert s.lambda_max == 0.05
    assert s.thick_min == 100
    assert s.thick_max == 240


def test_concrete_single_layer(mini_source):
    s = summarize_material(mini_source.material[1], country="CH")
    assert s.category == "Concrete"
    assert s.names == {"fr": "Beton B"}
    assert s.descriptions == {}
    assert s.lambda_min == s.lambda_max == 0.21
    assert s.thick_min == s.thick_max == 150


def test_material_without_layers_has_none_metrics(mini_source):
    s = summarize_material(mini_source.material[2])
    assert s.lambda_min is None
    assert s.thick_max is None
    assert s.usage == {"wall": False, "roof": False, "floor": False, "door": False}


def test_btk_summary_thickness_from_variations(mixed_source):
    from materialsdb.summary import summarize_material

    s = summarize_material(mixed_source.material[0], country="CH")

    assert s.type == "btk"
    assert s.thick_min == 200
    assert s.thick_max == 300
    assert s.lambda_min is None


def test_construction_summary_has_no_metrics(mixed_source):
    from materialsdb.summary import summarize_material

    s = summarize_material(mixed_source.material[1])

    assert s.type == "construction"
    assert s.lambda_min is None
    assert s.thick_max is None


def test_simple_summary_keeps_type_simple(mini_source):
    from materialsdb.summary import summarize_material

    assert summarize_material(mini_source.material[0]).type == "simple"
