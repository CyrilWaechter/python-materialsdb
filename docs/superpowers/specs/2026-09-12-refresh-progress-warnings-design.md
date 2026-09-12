# Refresh progress + cache warnings

Date: 2026-09-12
Status: approved (design)

Two follow-ups to the refresh fix (refresh now downloads producer files and can
take seconds to minutes on a slow line):

1. **Blocking overlay with progress + Cancel** while the cache updates.
2. **Empty-list and update-available notices.**

## Decisions

- **Overlay + busy bar**: modally covers the materials app while refreshing;
  progress line `downloading producer files 12/48` then `indexing…`; closes on
  completion with the summary in the status line. A Cancel button asks the
  background worker to stop between file downloads.
- **Progress granularity**: file-level (`done, total, name`). An in-flight
  `urlretrieve` is never aborted; cancel takes effect between downloads.
- **Server-managed job**: the HTTP request must get instant responses —
  `POST /api/refresh` starts a background thread and returns `{started: true}`;
  a second click while running → `409 {"error": "already running"}`.
  `GET /api/refresh/status` returns the job snapshot;
  `POST /api/refresh/cancel` sets the cancel flag (token-gated like all
  mutations).
- **Cancel semantics**: partial downloads stay in the cache (harmless — every
  file is validated on ingest); store ingestion is skipped; status is
  `cancelled` with the downloaded count reported.
- **Startup update check**: only spawned from the `main()` entry point
  (`python3 -m materialsdb.gui`) and after each successful refresh — never from
  `make_server`, so tests and library embedders get no network. It reuses
  `require_update()` against the cached indexes (fetch both index XMLs, compare
  `LastKnownDate`/`KnownVersion`); the result is a single boolean flag on
  `GuiState`, exposed via `GET /api/updates`. A network error just clears/keeps
  the flag quietly (never a user-facing failure).
- **Empty-list notice**: frontend-only — zero materials after list load shows
  the notice above the list; disappears as soon as any material is present.
- **Update banner**: one-line banner in the header next to refresh, hidden when
  the flag is false; disappears after a successful refresh.
- Tests never hit the network: they monkeypatch the download/index functions
  and poll the background job to completion inside the test (no sleeps beyond a
  bounded wait loop).

## Architecture

### 1. `src/materialsdb/cache.py` — progress callback

- `update_producers_data(url_list=MATERIALSDBINDEXURLLIST, on_progress=None)`
  where `on_progress(done, total, name)` counts producer-file downloads only
  (index fetches are phase steps, not counted).
- Internals: fetch both indexes first, compute per-index the companies needing
  download (via the existing `require_update` + existence check), build the
  flat list, then run a single download loop with one global total; deletions,
  index re-writes, and the `Report` remain as today. CLI examples keep working
  without the callback.

### 2. `src/materialsdb/gui/server.py` — job lifecycle + updates flag

- `GuiState` gains `refresh_job: dict | None` (`done`, `total`, `label`,
  `status`: running/complete/error/cancelled, `error: str | None`, `result`:
  the refresh payload).
- `_refresh`: if `state.refresh_job` running → 409; else snapshot the handler
  context, reset the job, `threading.Thread(daemon=True)` runs the worker:
  `update_producers_data(on_progress=…)` → `query.refresh(force=…)`; updates
  the dict; marks `updates_available = False` on success.
- `_refresh_cancel`: sets `status = "cancelled"` marker only if running.
- `GET /api/refresh/status`: returns an asdict view (no token needed — it is
  not mutating).
- `GET /api/updates`: returns `{"updates_available": bool}` (not token-gated).
- `main()` (module bottom, `if __name__ == "__main__"`): after the server is
  up, spawns a daemon thread that runs the update check once and writes the
  flag (exceptions swallowed into flag-False).

### 3. `src/materialsdb/gui/static/app.js` — overlay + banners

- `refresh` handler: open an overlay (`#refresh-overlay`, in `index.html`),
  busy CSS bar + label, Cancel button → `POST /api/refresh/cancel`;
  poll `GET /api/refresh/status` every 500 ms; render `done/total`; on
  `complete` → close + status summary; on `error` → close + `setStatus`
  error; on `cancelled` → close + "refresh cancelled (x/y downloaded)".
- After `loadMaterials`: zero materials → insert the empty-list notice into a
  dedicated `#hint` element ("No materials cached yet — click Refresh to
  download from materialsdb.org").
- Header banner `#updates` ("newer data available on materialsdb.org —
  refresh when convenient"), shown when `/api/updates` says true (fetched at
  page load and after each refresh), hidden otherwise.

## Error handling

- Overlay errors use the same JSON error contract (`"error"` string).
- Double refresh, token-less mutations: 403/409 as above.
- Server restarted between `start` and poll: the job dict dies with the
  process — the UI's 404/Closed on status poll closes the overlay and reports
  the connection loss.

## Testing

- `cache.py`: plan-and-download loop with a stubbed `urlopen`/`urlretrieve`
  (progress calls recorded; nothing downloadable → no download calls; deleted
  + re-write paths unchanged — probe names/order).
- `server.py`: worker completes end-to-end with monkeypatched cache/query
  (bounded poll loop reaching `complete`, summary fields present); second
  `POST /api/refresh` while first is running → 409; cancel before any download
  step → status `cancelled`, ingestion skipped; `/api/updates` reflects the
  flag; `make_server` alone never triggers a network check (assert no net fake
  was called while creating the server).
- Frontend: browser verification (Node harness cannot run real fetch streams).
- Docs: README gets one bullet on the overlay + one on the banner; release
  notes cover 3 items (refresh download fix, overlay, notices).
