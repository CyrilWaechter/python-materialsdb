"""materialsdb listener: receive material and construction pushes from the
local materialsdb picker.

The bpy surface is intentionally thin: a timer polls the local GUI server
and hands payloads to an undoable operator (tool.Ifc.Operator); all IFC work
goes through insert.py (pure ifcopenshell.api, CI-tested)."""

import atexit
import shutil
import subprocess
import typing
import webbrowser

import bpy
from bonsai import tool

from . import insert
from .discovery import ListenerClient, clear_gui_info, read_gui_info
from .read_construction import ReadError, read_construction_from_element

_CLIENT = None
_PENDING = None
_SERVER_PROC = None


class MATERIALSDB_OT_apply_push(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "materialsdb.apply_push"
    bl_label = "materialsdb apply push"
    bl_description = "Apply a pushed materialsdb payload to the open IFC model"
    # No UNDO flag: the WM does not push a titled step for operators invoked
    # from a timer, so the mutations would fold into a neighboring undo step.
    # The timer pushes an explicit, titled ed.undo_push instead.
    bl_options: typing.ClassVar[set[str]] = {"REGISTER"}

    def _execute(self, context):
        global _PENDING
        payload, _PENDING = _PENDING, None
        if payload is None:
            return
        if tool.Ifc.get() is None:
            _CLIENT.report("error", "no IFC model open in Bonsai")
            return
        action = payload.get("action")
        if action == "add_materials":
            count = insert.apply_add_materials(tool.Ifc.get(), payload)
            _CLIENT.report("applied", f"{count} material(s) added")
        elif action == "add_construction":
            result = insert.apply_add_construction(tool.Ifc.get(), payload)
            _link_pushed_types(tool.Ifc.get(), payload["construction"])
            _CLIENT.report(
                "applied",
                f"{result['types_created']} type(s) created, {result['sets_updated']} set(s) updated, "
                f"{result['materials_created']} material(s) created, "
                f"{result['placeholders_matched']} placeholder(s) matched",
            )
        else:
            _CLIENT.report("error", f"unknown action: {action}")


def _model_path():
    return str(tool.Ifc.get_path() or "")


def _undo_label(payload):
    action = payload.get("action")
    if action == "add_construction":
        return f"materialsdb: add construction '{payload['construction'].get('name', '')}'"
    if action == "add_materials":
        return f"materialsdb: add {len(payload.get('materials') or [])} material(s)"
    return "materialsdb: apply push"


def _link_pushed_types(file, construction):
    """Bonsai's outliner is the stock Blender outliner: an IFC element shows
    only when a Blender object exists for it, placed in the IfcTypeProduct
    collection. Mirror bonsai's own type-creation flow (link + name +
    collector) for pushed types; idempotent via get_object."""
    for cls in construction["types"]:
        for element in file.by_type(cls):
            if element.Name != construction["name"]:
                continue
            if tool.Ifc.get_object(element) is not None:
                continue
            obj = bpy.data.objects.new(element.Name, None)
            tool.Ifc.link(element, obj)
            tool.Root.set_object_name(obj, element)
            tool.Collector.assign(obj)
            bpy.context.view_layer.update()


def _poll_timer():
    global _CLIENT, _PENDING  # noqa: PLW0602
    if _CLIENT is None:
        return None  # listener stopped: unregister the timer
    try:
        path = _model_path()
        if path != _CLIENT.registered_path:
            _CLIENT.register(path)
        payload = _CLIENT.poll()
        if payload is not None:
            _PENDING = payload
            try:
                bpy.ops.materialsdb.apply_push()
            except Exception as err:  # noqa: BLE001 - report; never kill the poll loop
                try:
                    _CLIENT.report("error", str(err))
                except Exception:  # noqa: BLE001, S110
                    pass
            else:
                # titled, isolated undo step (the WM pushes none for
                # timer-invoked operators); bookkeeping is best-effort
                try:
                    bpy.ops.ed.undo_push(message=_undo_label(payload))
                except Exception:  # noqa: BLE001, S110
                    pass
    except Exception as err:  # noqa: BLE001 - a failed push must not kill the timer
        try:
            _CLIENT.report("error", str(err))
        except Exception:  # noqa: BLE001, S110
            pass
    return 1.0


def _prefs():
    return getattr(bpy.context.preferences.addons.get(__package__), "preferences", None)


def _find_python():
    """The configured interpreter, else autodiscover python3/python/py."""
    prefs = _prefs()
    if prefs is not None and prefs.interpreter.strip():
        return prefs.interpreter.strip()
    return shutil.which("python3") or shutil.which("python") or shutil.which("py")


def _server_port():
    prefs = _prefs()
    return prefs.port if prefs is not None else 0


def _server_url():
    info = read_gui_info()
    return f"http://127.0.0.1:{info[0]}" if info else None


def _server_alive(url):
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=2)
    except Exception:  # noqa: BLE001 - dead/stale server is the normal case
        return False
    return True


def _kill_server():
    global _SERVER_PROC
    if _SERVER_PROC is not None:
        _SERVER_PROC.terminate()
        _SERVER_PROC = None


class MATERIALSDB_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    interpreter: bpy.props.StringProperty(
        name="Python interpreter",
        description="Interpreter with python-materialsdb installed (empty = autodiscover python3/python/py on PATH)",
        subtype="FILE_PATH",
    )
    port: bpy.props.IntProperty(name="Port", description="0 lets the server pick a free port", default=0)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "interpreter")
        layout.prop(self, "port")
        layout.label(text="Requires: pip install python-materialsdb (in that interpreter)")


class MATERIALSDB_OT_start_server(bpy.types.Operator):
    bl_idname = "materialsdb.start_server"
    bl_label = "Start server & open picker"
    bl_description = "Start the local materialsdb GUI server and open it in your browser"
    bl_options: typing.ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context):
        global _SERVER_PROC
        url = _server_url()
        if url is not None and _server_alive(url):
            webbrowser.open(url)
            self.report({"INFO"}, f"server already running: {url}")
            return {"FINISHED"}
        _kill_server()
        clear_gui_info()
        python = _find_python()
        if python is None:
            self.report({"ERROR"}, "no python interpreter found; set one in the add-on preferences")
            return {"CANCELLED"}
        try:
            subprocess.run([python, "-c", "import materialsdb"], capture_output=True, timeout=30, check=True)
        except (subprocess.SubprocessError, OSError):
            self.report({"ERROR"}, f"pip install python-materialsdb in {python} (import failed)")
            return {"CANCELLED"}
        args = [python, "-m", "materialsdb.gui", "--no-browser"]
        if _server_port():
            args += ["--port", str(_server_port())]
        _SERVER_PROC = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(_kill_server)
        import time

        deadline = time.time() + 15
        while time.time() < deadline:
            url = _server_url()
            if url is not None:
                break
            time.sleep(0.2)
        if url is None:
            self.report({"ERROR"}, "server did not start — check Blender console for its output")
            _kill_server()
            return {"CANCELLED"}
        webbrowser.open(url)
        self.report({"INFO"}, f"server started: {url}")
        return {"FINISHED"}


class MATERIALSDB_OT_stop_server(bpy.types.Operator):
    bl_idname = "materialsdb.stop_server"
    bl_label = "Stop server"
    bl_description = "Stop the local materialsdb GUI server"
    bl_options: typing.ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context):
        if _SERVER_PROC is None:
            self.report({"INFO"}, "server not started from here")
            return {"FINISHED"}
        _kill_server()
        clear_gui_info()
        self.report({"INFO"}, "server stopped")
        return {"FINISHED"}


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


class MATERIALSDB_OT_send_construction(bpy.types.Operator):
    bl_idname = "materialsdb.send_construction"
    bl_label = "materialsdb send construction"
    bl_description = "Push the active object's type layer set to the composer (read-only)"
    bl_options: typing.ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context):
        obj = context.active_object
        element = tool.Ifc.get_entity(obj) if obj is not None else None
        if element is None:
            self.report({"ERROR"}, "select an IFC element")
            return {"CANCELLED"}
        try:
            construction = read_construction_from_element(element)
        except ReadError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        try:
            ListenerClient().send_to_composer(construction)
        except RuntimeError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        except Exception as err:  # noqa: BLE001 - surface, never crash Blender
            self.report({"ERROR"}, f"materialsdb-gui not reachable: {err}")
            return {"CANCELLED"}
        placeholders = sum(1 for layer in construction["layers"] if layer["placeholder"])
        self.report(
            {"INFO"},
            f"sent {len(construction['layers'])} layer(s) ({placeholders} model material) to the composer",
        )
        return {"FINISHED"}


class MATERIALSDB_PT_panel(bpy.types.Panel):
    bl_label = "materialsdb"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "materialsdb"

    def draw(self, context):
        running = _CLIENT is not None
        layout = self.layout
        layout.operator("materialsdb.start_server", text="Start server & open picker")
        if _SERVER_PROC is not None:
            layout.operator("materialsdb.stop_server", text="Stop server")
        url = _server_url()
        if url:
            layout.label(text=url)
        layout.operator("materialsdb.toggle_listener", text="Stop listener" if running else "Start listener")
        layout.operator("materialsdb.send_construction", text="Send type to composer")
        layout.label(text="listening" if running else "stopped", icon="LINKED" if running else "UNLINKED")


def register():
    bpy.utils.register_class(MATERIALSDB_Preferences)
    bpy.utils.register_class(MATERIALSDB_OT_toggle_listener)
    bpy.utils.register_class(MATERIALSDB_OT_apply_push)
    bpy.utils.register_class(MATERIALSDB_OT_send_construction)
    bpy.utils.register_class(MATERIALSDB_OT_start_server)
    bpy.utils.register_class(MATERIALSDB_OT_stop_server)
    bpy.utils.register_class(MATERIALSDB_PT_panel)


def unregister():
    global _CLIENT
    _CLIENT = None
    _kill_server()
    if bpy.app.timers.is_registered(_poll_timer):
        bpy.app.timers.unregister(_poll_timer)
    bpy.utils.unregister_class(MATERIALSDB_PT_panel)
    bpy.utils.unregister_class(MATERIALSDB_OT_stop_server)
    bpy.utils.unregister_class(MATERIALSDB_OT_start_server)
    bpy.utils.unregister_class(MATERIALSDB_OT_send_construction)
    bpy.utils.unregister_class(MATERIALSDB_OT_apply_push)
    bpy.utils.unregister_class(MATERIALSDB_OT_toggle_listener)
    bpy.utils.unregister_class(MATERIALSDB_Preferences)
