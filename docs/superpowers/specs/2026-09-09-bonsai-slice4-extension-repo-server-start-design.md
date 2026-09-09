# Design: Bonsai slice 4 — extension repository + server start from Blender

Date: 2026-09-09
Status: Approved (brainstorming session)
Base spec: `2026-09-09-bonsai-slice3-construction-roundtrip-design.md` (round-trip)

## Goals

1. One-click install + update of the Blender extension via a Blender
   **extension repository**, published by CI on each GitHub release.
2. Start the materialsdb GUI server directly from the Blender panel (the GUI
   needs an external interpreter: lxml + the package are not importable in
   Blender's Python).

## Decisions

- **Hosting: GitHub Pages on this repo.** CI publishes `index.json` + zips
  (+ html listing) to the `gh-pages` branch. Users add
  `https://cyrilwaechter.github.io/python-materialsdb/` once under
  *Preferences ▸ Get Extensions ▸ Add Remote Repository*; install and
  updates then live in Blender's UI. Manual zip install remains a fallback.
- **Publish trigger: GitHub release.** The tag (without `v`) becomes the
  manifest version, so Blender's update comparison is tag-accurate. Master
  builds stay local (no unstable channel).
- **Version injection**: `dev_utils/build_bonsai_addon.py --version X`
  rewrites `blender_manifest.toml`'s `version` before zipping and names the
  output `materialsdb_listener-X.zip`. Local builds (no flag) are unchanged.
- **Repository generation**: CI downloads a Linux Blender tarball (bonsai
  daily pattern) and runs
  `blender --command extension server-generate --repo-dir repo --html` —
  the official generator guarantees `index.json` schema correctness. Deploy
  via a gh-pages action on the workflow's own GITHUB_TOKEN.
- **Server start: subprocess from an external interpreter.** Blender's
  Python lacks lxml/the package, so the GUI cannot run in-thread.
  - Add-on preferences (`MATERIALSDB_Preferences`): `interpreter` path
    (empty = autodiscover `python3` → `python` → `py` via `shutil.which`)
    and `port` (0 = ephemeral; recorded in `gui.json` anyway).
  - Preflight: `[interp, "-c", "import materialsdb"]` → on failure report
    "pip install python-materialsdb in <interpreter>" and abort.
  - Stale `gui.json` (from a dead previous server) is deleted before start
    (new `discovery.clear_gui_info()`); a fresh file appearing within 15 s
    signals readiness.
  - Spawn `python -m materialsdb.gui --no-browser`; Blender opens the URL
    with `webbrowser.open` after readiness (exactly one tab). Port + url
    read from the fresh `gui.json`.
  - The child handle is tracked; `materialsdb.stop_server` terminates it and
    clears `gui.json` (SIGTERM skips the server's own cleanup);
    `atexit` kills it on Blender exit.
  - Listener flow unchanged: it re-registers when `gui.json` appears.
- **Server already running**: if a `gui.json` exists AND a poll of
  `<port>/` succeeds, start reports "server already running" and just opens
  the browser instead of spawning a duplicate.

## Components

- `dev_utils/build_bonsai_addon.py`: `--version` flag (manifest rewrite +
  versioned zip name).
- `bonsai_addon/discovery.py`: `clear_gui_info()`.
- `bonsai_addon/__init__.py`: `MATERIALSDB_Preferences` (AddonPreferences:
  interpreter, port), `MATERIALSDB_OT_start_server`,
  `MATERIALSDB_OT_stop_server`, `_find_python()`, `_server_url()` helpers;
  panel Server section above the listener controls; `atexit` cleanup.
- `.github/workflows/extension_repo.yml`: release → build (--version) →
  blender server-generate → gh-pages deploy.
- `README.md`: repository URL + install/update + server button +
  prerequisite (`pip install python-materialsdb`).

## Error handling

| Condition | Behaviour |
|---|---|
| no interpreter found on PATH | start reports "no python interpreter found; set one in preferences" |
| `import materialsdb` preflight fails | "pip install python-materialsdb in <interpreter>" |
| server dies before `gui.json` appears (15 s) | "server did not start — check Blender console for its output" |
| stale `gui.json` present at start | deleted silently, fresh start proceeds |
| `gui.json` valid + server alive | "server already running" + browser open, no new process |
| stop with no running child | no-op report |

## Testing

- CI: build `--version` test (zip contains the manifest with the injected
  version + versioned filename); `clear_gui_info` test (deletes an existing
  file, idempotent on absence).
- bpy parts (prefs, operators): manual checklist — start → browser opens →
  listener registers → send construction → stop → `gui.json` gone; already-
  running case; missing-package case.
- Workflow: validated on the first release run (flagged as such; repository
  URL usable after the one-time Pages enablement).

## Non-goals

Auto-managed venv for the server deps; unstable/master channel; non-GitHub
hosting; GUI server auto-start on Blender launch.
