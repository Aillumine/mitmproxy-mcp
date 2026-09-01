"""Resolve the packaged SPA build directory."""

from pathlib import Path


def webui_path() -> Path | None:
    root = Path(__file__).resolve().parent.parent / "webui"
    if (root / "index.html").is_file():
        return root
    return None
