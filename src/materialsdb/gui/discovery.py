"""Discovery file bridging the GUI server and local listeners (Bonsai add-on).

The GUI writes {port, token, pid} into the shared cache folder at startup
and removes it on clean shutdown; listeners read it to authenticate. The
path comes from cache.get_cache_folder() (APPDATA / XDG_CACHE_HOME aware)."""

import json
import os
from pathlib import Path

from materialsdb import cache


def listener_info_path() -> Path:
    return cache.get_cache_folder() / "gui.json"


def write_listener_info(port: int, token: str) -> Path:
    path = listener_info_path()
    path.write_text(
        json.dumps({"port": int(port), "token": token, "pid": os.getpid()}),
        encoding="utf-8",
    )
    return path


def remove_listener_info() -> None:
    listener_info_path().unlink(missing_ok=True)
