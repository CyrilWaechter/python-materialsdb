"""materialsdb listener: receive pushes from the local materialsdb picker.

The bpy surface is intentionally thin: a timer polls the local GUI server,
and all IFC work goes through insert.apply_add_materials (pure ifcopenshell,
CI-tested)."""

import typing

import bpy
from bonsai import tool
from bonsai.bim import handler

from . import insert
from .discovery import ListenerClient

_CLIENT = None


def _active_ifc_file():
    """The IFC file of the model currently open in Bonsai (None if none open)."""
    return tool.Ifc.get()


def _model_path():
    return str(tool.Ifc.get_path() or "")


def _poll_timer():
    global _CLIENT  # noqa: PLW0602
    if _CLIENT is None:
        return None  # listener stopped: unregister the timer
    try:
        path = _model_path()
        if path != _CLIENT.registered_path:
            _CLIENT.register(path)
        payload = _CLIENT.poll()
        if payload is not None:
            file = _active_ifc_file()
            if file is None:
                raise RuntimeError("no IFC model open in Bonsai")
            count = insert.apply_add_materials(file, payload)
            try:
                handler.refresh_ui_data()
            except Exception:  # noqa: BLE001, S110
                pass  # cosmetic; the insert itself succeeded
            _CLIENT.report("applied", f"{count} material(s) added")
    except Exception as err:  # noqa: BLE001 - a failed push must not kill the timer
        try:
            _CLIENT.report("error", str(err))
        except Exception:  # noqa: BLE001, S110
            pass
    return 1.0


class MATERIALSDB_OT_toggle_listener(bpy.types.Operator):
    bl_idname = "materialsdb.toggle_listener"
    bl_label = "materialsdb listener"
    bl_description = "Start or stop receiving pushes from the local materialsdb picker"
    bl_options: typing.ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context):
        global _CLIENT
        if _CLIENT is None:
            _CLIENT = ListenerClient()
            try:
                _CLIENT.register(_model_path())
            except Exception as err:  # noqa: BLE001
                _CLIENT = None
                self.report({"ERROR"}, f"materialsdb-gui not reachable: {err}")
                return {"CANCELLED"}
            bpy.app.timers.register(_poll_timer, persistent=True)
            self.report({"INFO"}, "materialsdb listener started")
        else:
            _CLIENT = None
            self.report({"INFO"}, "materialsdb listener stopped")
        return {"FINISHED"}


class MATERIALSDB_PT_panel(bpy.types.Panel):
    bl_label = "materialsdb"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "materialsdb"

    def draw(self, context):
        running = _CLIENT is not None
        layout = self.layout
        layout.operator("materialsdb.toggle_listener", text="Stop listener" if running else "Start listener")
        layout.label(text="listening" if running else "stopped", icon="LINKED" if running else "UNLINKED")


def register():
    bpy.utils.register_class(MATERIALSDB_OT_toggle_listener)
    bpy.utils.register_class(MATERIALSDB_PT_panel)


def unregister():
    global _CLIENT
    _CLIENT = None
    if bpy.app.timers.is_registered(_poll_timer):
        bpy.app.timers.unregister(_poll_timer)
    bpy.utils.unregister_class(MATERIALSDB_PT_panel)
    bpy.utils.unregister_class(MATERIALSDB_OT_toggle_listener)
