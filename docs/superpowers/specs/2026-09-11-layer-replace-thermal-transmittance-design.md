# Construction maker: layer replace + ThermalTransmittance psets

Date: 2026-09-11
Status: approved (design)

Two construction-maker enhancements:

1. Replace the material of an existing layer (double-click and pencil in the
   material cell).
2. Store the computed U-value in `Pset_*Common.ThermalTransmittance` on push
   to Bonsai, with per-type Rsi/Rse (wall / floor / roof directions).

## Decisions

- **Replace interaction**: pencil button and double-click, both on the
  material/name cell only. Thickness cells keep their own controls. Same
  chooser flow as "add layer", in replace mode targeting the row index.
- **Placeholder rows are replaceable**: picking a materialsdb material on a
  placeholder row converts it to a normal layer (`material_id` set,
  `placeholder` dropped, thickness kept).
- **U-value travels server-side**: `build_add_construction_payload` computes
  the values via `construction.u_value()` — no trust in client numbers.
- **Generic usage = per-direction values**: payload carries
  `u_values: {IfcWallType: float, IfcSlabType: float, IfcRoofType: float}`.
  Explicit usage (wall / floor / roof) → single entry for its own type.
  Each direction uses the matching Rsi/Rse from `RESISTANCE_PRESETS`.
- **No U → skip**: when λ cannot be resolved for some layer, the pset is
  simply not written (no placeholder properties).
- **Upsert updates in place**: on re-push, found psets get their property
  edited (mirrors the layer-set upsert semantics).
- **Round-trip stays out of scope**: `read_construction.py` does not read
  `ThermalTransmittance` back (Construction has no u field).

## Architecture

### 1. `src/materialsdb/construction.py` — direction-aware math

- Extract the layer loop of `u_value()` into
  `_u_for_direction(construction, store_, direction, preset) -> UResult`
  (R-sum, contributions, missing-lambda list, `u = 1 / (rsi + r_sum + rse)`).
- `u_value()` delegates to it with `_direction(construction.design_usage)`.
  Its public behaviour (UResult, callers in server.py) is unchanged.
- New helper `u_values_by_type(construction, store_, types, preset) -> dict[str, float | None]`:
  maps each type class to its direction
  (`IfcWallType → wall`, `IfcSlabType → floor`, `IfcRoofType → roof`) and
  returns `{cls: result.u}`. Shared layers recompute trivially — R-sum per
  direction is cheap, no caching needed.

### 2. `src/materialsdb/gui/listener.py` — payload

In `build_add_construction_payload`, after layers resolve:

```python
"u_values": {
    cls: u for cls, u in cm.u_values_by_type(
        construction, store_, types).items() if u is not None
},
```

(per-type `None` is simply omitted). No GUI change; the push body layout is
untouched.

### 3. `bonsai_addon/insert.py` — pset writing

`_THERMAL_TRANSMITTANCE_PSET = {"IfcWallType": "Pset_WallCommon",
"IfcSlabType": "Pset_SlabCommon", "IfcRoofType": "Pset_RoofCommon"}`.

After the material assignment loop:

- for each target, look up `payload["construction"].get("u_values", {})`
  keyed by the target's `is_a()` name;
- `pset.add_pset` (find-or-create) then `pset.edit_pset` setting
  `{"ThermalTransmittance": u}` on the IfcTypeProduct;
- summary dict gains `psets_written`;
- absent/empty `u_values` → no writes.

Status line in the panel gains the `psets_written` count via the existing
summary formatting.

### 4. GUI — `app-constructions.js` replace flow

- Material cell HTML: material name plus a small ✏ pencil
  (`title="replace material"`), inside the material/name cell only.
- `dblclick` on the material/name cell (not on the pencil, which uses plain
  click) and both open the chooser in replace mode targeting the row index
  (payload carries `replace_index`); the pending ✓/✗ LED resets on every
  new pick, exactly like an add-layer pick.
- On the composer pick with `replace_index >= 0`: keep `thickness_m`, drop
  `placeholder`, set `material_id`, then `attachLayerChoices` +
  `renderLayers` + `refreshU` (identical post-pick sequence as add-layer);
  parent-side listener mirrors add-layer (no new endpoint).
- Save/push name handling unchanged (a replaced placeholder layer simply
  serialises as a normal layer).

## Error handling

- Unknown/missing material on replace pick: existing chooser validation
  already prevents it.
- Wrong-shaped `u_values` (non-dict, non-float): treated as absent — never
  fatal for the push.
- `u_values_by_type` never raises for missing λ (returns `None` per type,
  omitted from payload).

## Testing

- CI unit tests (`tests/test_construction.py`):
  - `u_values_by_type` returns per-direction values for generic usage
    (wall ≠ floor ≠ roof Rsi/Rse), single direction for explicit usage;
  - unresolvable λ → `None` entries.
- CI add-on tests (`tests/test_bonsai_insert.py`):
  - wall payload writes `Pset_WallCommon.ThermalTransmittance`;
  - generic payload writes the three `*_Common` psets with the respective
    per-direction U;
  - missing-λ payload writes nothing;
  - re-push with a different U updates the property value;
  - payload without `u_values` writes nothing.
- In-Blender harness: one extra assert — after
  `test_construction_roundtrip_from_model`, a pushed construction exposes
  `Pset_WallCommon.ThermalTransmittance`.
- GUI harness (`picker_core_harness.mjs`): replace pick replaces the target
  row and converts a placeholder row.

## Documentation

- README feature list: two bullet lines (layer replace; U-value psets).
- Release notes: next release (post-0.2.0) will describe both.
- Version bump deferred to the release step.
