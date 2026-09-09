# Crosspost for the OSArch discussion (comment 19855 thread)

*Crosspost from [python-materialsdb 0.2 — from a materials library to a materials workflow](ARTICLE_URL)*

---

Answering the questions in this thread — the library grew a lot since the
original announcement:

**@Owura_qu**: yes — materialsdb materials can now land straight into Bonsai,
with Name, Description, Category and the full materialsdb identity/thermal
property sets. It ships as a **Blender extension** you can install once from
an extension repository: in *Preferences ▸ Get Extensions ▸ ⌄ ▸ Add Remote
Repository* add `https://cyrilwaechter.github.io/python-materialsdb/`, then
install *materialsdb listener* (updates show up in the same panel).

The workflow: a small local web app (`materialsdb-gui`) lists all cached
materialsdb.org materials — you filter by manufacturer/category/conductivity,
pick whole materials or specific manufacturer thicknesses, and one click
registers them in your open IFC model. There is also a **construction
maker**: compose layers with a live U-value (ISO 6946 / SIA 180), then send
the construction into the model as an `IfcWallType`/`IfcSlabType`/
`IfcRoofType` with its `IfcMaterialLayerSet`. And a round-trip: select a wall
(or its type) in Bonsai, push it back to the composer, adjust it, push it
back into the same layer set — undoable with a titled step.

**@steverugi** — importing materials from a text table is on the roadmap now
that all the catalog/store flows exist; pushed it to the top of the
what's-next list.

Article with screenshots: <ARTICLE_URL> · Code: <https://github.com/CyrilWaechter/python-materialsdb> · PyPI: `pip install python-materialsdb`

Tagged: Building Energy Modeling (BEM), materials, Bonsai, IfcProjectLibrary, opendata
