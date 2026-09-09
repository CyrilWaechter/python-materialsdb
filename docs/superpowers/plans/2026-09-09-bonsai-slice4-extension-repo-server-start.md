# Bonsai Slice 4 — Extension Repository + Server Start from Blender Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a Blender extension repository from CI on each release (one-click install/update in Blender) and add panel buttons that start/stop the materialsdb GUI server from an external interpreter.

**Architecture:** The build script gains `--version` (manifest rewrite + versioned zip name); CI (on release) builds the zip, runs Blender's `extension server-generate` for a static `index.json`, and deploys the directory to `gh-pages`. The add-on gains `MATERIALSDB_Preferences` (interpreter, port), `start_server`/`stop_server` operators spawning `python -m materialsdb.gui --no-browser` as a tracked subprocess with stale-`gui.json` cleanup and browser open after readiness.

**Tech Stack:** GitHub Actions + `peaceiris/actions-gh-pages`, Blender 5.2 `extension server-generate`, `subprocess`/`shutil.which`/`webbrowser`/`atexit` (stdlib), existing discovery-file contract.

## Global Constraints

- Server prerequisite surfaced, never auto-installed: the configured interpreter must have `pip install python-materialsdb`; preflight `[interp, "-c", "import materialsdb"]` gates start.
- Stale `gui.json` deleted before start; readiness = a FRESH file within 15 s; `--no-browser` + Blender `webbrowser.open` (exactly one tab).
- `materialsdb.stop_server` terminates the child AND clears `gui.json` (SIGTERM skips the server's own cleanup); `atexit` kills the child on Blender exit.
- Already-running server: valid `gui.json` + successful `GET /` → "server already running" + browser open, no duplicate process.
- Workflow publishes ONLY on `release: published`; tag without `v` becomes the manifest version; repo URL is `https://cyrilwaechter.github.io/python-materialsdb/`.
- bpy files ruff-linted (line 120), ty stays blind to `bonsai_addon/`; local pytest `python3 -m pytest -p no:pytest-blender -q`; harness `pytest bonsai_addon/test -q`.
- `dev_utils/build_bonsai_addon.py` default behavior (no flag) must stay byte-identical (`dist/materialsdb_listener.zip`).
- Untracked scratch files at repo root are NEVER added/fixed.

---

### Task 1: Build script `--version` flag

**Files:**
- Modify: `dev_utils/build_bonsai_addon.py`
- Test: `tests/test_build_bonsai_addon.py` (new)

**Interfaces:**
- Produces: `build(version: str | None = None, out_dir: Path | None = None) -> Path` (refactored from `main()`; `main()` keeps CLI behavior — argparse with `--version`, default output `dist/materialsdb_listener.zip`); with a version: manifest `version` rewritten in the zipped copy and output named `materialsdb_listener-<version>.zip`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_bonsai_addon.py`:

```python
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev_utils"))

import build_bonsai_addon  # ty: ignore[unresolved-import] - dev_util on a side path


def test_default_build_unchanged(tmp_path):
    out = build_bonsai_addon.build(out_dir=tmp_path)

    assert out.name == "materialsdb_listener.zip"
    with zipfile.ZipFile(out) as zf:
        assert "blender_manifest.toml" in zf.namelist()
        assert "blender_manifest.toml".replace(".", "/") not in zf.namelist()  # manifest at zip ROOT


def test_version_injected_into_manifest_and_name(tmp_path):
    out = build_bonsai_addon.build(version="1.2.3", out_dir=tmp_path)

    assert out.name == "materialsdb_listener-1.2.3.zip"
    with zipfile.ZipFile(out) as zf:
        manifest = zf.read("blender_manifest.toml").decode("utf-8")
        assert 'version = "1.2.3"' in manifest
```

(The module-level `ROOT`/`SRC` constants in the script stay; `build()` takes `out_dir` so tests never touch the real `dist/`. The `sys.path` + `# ty: ignore[unresolved-import]` pattern matches the other side-path imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_build_bonsai_addon.py -q`
Expected: FAIL — `AttributeError: module 'build_bonsai_addon' has no attribute 'build'`.

- [ ] **Step 3: Implement**

Replace `dev_utils/build_bonsai_addon.py` with:

```python
"""Zip bonsai_addon/ as a Blender extension for 'Install from Disk'.

The manifest must sit at the zip root, so archive names are relative to
bonsai_addon/ itself. CI passes --version on releases: the manifest's
version is rewritten for the zipped copy and the zip gains a versioned
name (Blender's update flow compares manifest versions)."""

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bonsai_addon"


def build(version: str | None = None, out_dir: Path | None = None) -> Path:
    out_dir = Path(out_dir) if out_dir else ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    name = f"materialsdb_listener-{version}.zip" if version else "materialsdb_listener.zip"
    out = out_dir / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(SRC.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                if version and file.name == "blender_manifest.toml":
                    zf.writestr(
                        file.relative_to(SRC),
                        file.read_text("utf-8").replace(
                            'version = "0.1.0"', f'version = "{version}"'
                        ),
                    )
                else:
                    zf.write(file, file.relative_to(SRC))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="extension version; injected into the manifest and the zip name")
    args = parser.parse_args()
    print(build(version=args.version))


if __name__ == "__main__":
    main()
```

NOTE: the `version = "0.1.0"` literal must match `bonsai_addon/blender_manifest.toml`'s current version line exactly — read the manifest first and adjust the `.replace` source string if it differs.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_build_bonsai_addon.py -q && python3 dev_utils/build_bonsai_addon.py`
Expected: tests PASS; default zip still produced at `dist/materialsdb_listener.zip`.

- [ ] **Step 5: Commit**

```bash
git add dev_utils/build_bonsai_addon.py tests/test_build_bonsai_addon.py
git commit -m "feat(dev): build --version injects manifest version and versioned zip name"
```

---

### Task 2: `discovery.clear_gui_info()`

**Files:**
- Modify: `bonsai_addon/discovery.py`
- Test: `tests/test_bonsai_discovery.py`

**Interfaces:**
- Produces: `clear_gui_info() -> None` — deletes `<cache folder>/gui.json`, missing-ok (used by Task 3's start/stop operators).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bonsai_discovery.py`:

```python
def test_clear_gui_info_removes_stale_file(tmp_path, monkeypatch):
    gui_json = tmp_path / "materialsdb" / "gui.json"
    gui_json.parent.mkdir(parents=True)
    gui_json.write_text('{"port": 1, "token": "t", "pid": 2}', encoding="utf-8")
    monkeypatch.setattr(discovery, "_cache_folder", lambda: tmp_path / "materialsdb")

    discovery.clear_gui_info()

    assert not gui_json.exists()
    discovery.clear_gui_info()  # idempotent on absence
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_discovery.py -q`
Expected: FAIL — `AttributeError: module 'discovery' has no attribute 'clear_gui_info'`.

- [ ] **Step 3: Implement**

Add to `bonsai_addon/discovery.py` (after `read_gui_info`):

```python
def clear_gui_info():
    """Remove a (possibly stale) discovery file; the server rewrites one on
    start, and a dead server's leftover would point clients at a dead port."""
    (_cache_folder() / "gui.json").unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_discovery.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/discovery.py tests/test_bonsai_discovery.py
git commit -m "feat(bonsai): clear_gui_info for stale discovery files"
```

---

### Task 3: Add-on preferences + start/stop server operators

**Files:**
- Modify: `bonsai_addon/__init__.py`
- Test: none (bpy; verified by Task 5's manual checklist + gates)

**Interfaces:**
- Consumes: `read_gui_info`, `clear_gui_info` (discovery), `shutil.which`, `subprocess.Popen`, `webbrowser.open`, `atexit`.
- Produces: `MATERIALSDB_Preferences` (`interpreter: StringProperty`, `port: IntProperty`), `bpy.ops.materialsdb.start_server()`, `bpy.ops.materialsdb.stop_server()`, `_find_python()` (str | None), `_server_url() -> str | None` (from `read_gui_info`), module global `_SERVER_PROC`.

- [ ] **Step 1: Implement**

In `bonsai_addon/__init__.py`:

Add stdlib imports to the typing-group area:

```python
import atexit
import shutil
import subprocess
import webbrowser
```

Extend the discovery import to:

```python
from .discovery import ListenerClient, clear_gui_info, read_gui_info
```

Module global (after `_PENDING = None`):

```python
_SERVER_PROC = None
```

Helpers, preferences and operators (place before `MATERIALSDB_OT_toggle_listener`):

```python
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
```

Panel `draw` — add the Server section ABOVE the listener row:

```python
        layout.operator("materialsdb.start_server", text="Start server & open picker")
        if _SERVER_PROC is not None:
            layout.operator("materialsdb.stop_server", text="Stop server")
        url = _server_url()
        if url:
            layout.label(text=url)
```

`register()`/`unregister()`:

```python
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
```

`unregister` calling `_kill_server()` stops a Blender-spawned server when the
extension is disabled — intentional (the atexit path covers normal quit).

- [ ] **Step 2: Gates**

Run: `ruff check --exclude src/materialsdb/classes.py bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && python3 -m pytest -p no:pytest-blender -q && ty check --project .`
Expected: all clean; main suite unchanged.

- [ ] **Step 3: Commit**

```bash
git add bonsai_addon/__init__.py
git commit -m "feat(bonsai): start/stop the GUI server from the panel (preferences for interpreter)"
```

---

### Task 4: CI workflow — extension repository

**Files:**
- Create: `.github/workflows/extension_repo.yml`

**Interfaces:**
- Consumes: `dev_utils/build_bonsai_addon.py --version` (Task 1); Blender 5.2 tarball.
- Produces: `gh-pages` branch containing `index.json`, `materialsdb_listener-<version>.zip`, html listing.

- [ ] **Step 1: Implement the workflow**

Create `.github/workflows/extension_repo.yml`:

```yaml
name: Extension repository

on:
  release:
    types: [published]

permissions:
  contents: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Download Blender
        run: |
          wget -q -O blender.tar.xz https://download.blender.org/release/Blender5.2/blender-5.2.0-linux-x64.tar.xz
          tar -xf blender.tar.xz
          echo "$(find blender-*/ -maxdepth 0 -exec readlink -f {} \;)" >> "$GITHUB_PATH"
      - name: Build extension with the release version
        run: python3 dev_utils/build_bonsai_addon.py --version "${GITHUB_REF_NAME#v}"
      - name: Assemble repository directory
        run: |
          mkdir repo
          cp dist/materialsdb_listener-*.zip repo/
      - name: Generate repository listing
        run: blender --command extension server-generate --repo-dir repo --html
      - name: Deploy to gh-pages
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_branch: gh-pages
          publish_dir: repo
          force_orphan: true
          commit_message: "Extension repository ${{ github.event.release.tag_name }}"
```

Add `.gitignore` entry if `repo/` could leak locally — it cannot (created only in CI); skip.

- [ ] **Step 2: Sanity checks**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/extension_repo.yml'))" 2>/dev/null || python3 -c "print('yaml module absent — visual check only')"`
Also run the build with a fake version locally to prove the CI command works: `python3 dev_utils/build_bonsai_addon.py --version "9.9.9-test"` → expect `dist/materialsdb_listener-9.9.9-test.zip` (then delete that stray zip: `rm dist/materialsdb_listener-9.9.9-test.zip`).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/extension_repo.yml
git commit -m "ci: publish the Blender extension repository on release"
```

---

### Task 5: Docs + full gates + zip

**Files:**
- Modify: `README.md`, `AGENTS.md`

- [ ] **Step 1: README**

Replace the current install sentence block in the "Bonsai integration :" section (from `Install dist/materialsdb_listener.zip (build it with python3 dev_utils/build_bonsai_addon.py) via Blender's *Extensions ▸ Install from Disk*, start the listener…`) with:

```markdown
Install it from the extension repository: in Blender, *Preferences ▸
Get Extensions ▸ ⌄ ▸ Add Remote Repository* and add
`https://cyrilwaechter.github.io/python-materialsdb/`, then install
*materialsdb listener* (updates appear in the same panel). Manual
install stays available: build `dist/materialsdb_listener.zip` with
`python3 dev_utils/build_bonsai_addon.py` and use
*Extensions ▸ Install from Disk*. Start the listener from the
materialsdb panel in the 3D-view sidebar. The panel can also start the
GUI server itself (*Start server & open picker*) — it needs
`pip install python-materialsdb` in a system Python (set the
interpreter in the add-on preferences if autodiscovery picks the wrong
one).
```

Keep the following sentences (run `materialsdb-gui` as usual… etc.) intact after it.

In `AGENTS.md`, under the bonsai_addon bullet, append:

```markdown
 Releases publish the Blender extension repository to gh-pages
 (`.github/workflows/extension_repo.yml`): the tag (without `v`) becomes
 the manifest version; one-time setup = enable GitHub Pages from the
 `gh-pages` branch.
```

- [ ] **Step 2: Full gates**

Run: `python3 -m pytest -p no:pytest-blender -q && ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && ty check --project . && pytest bonsai_addon/test -q && python3 dev_utils/build_bonsai_addon.py`
Expected: all green; default zip rebuilt.

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: extension repository install and in-Blender server start"
```
