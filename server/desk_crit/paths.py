"""Where the Lumr home is, and how long a recording runs.

Derived from Lumr Studio's engine/paths.py and engine/render.py, slimmed to the
two functions Desk Crit needs. ``lumr_home`` resolves the same folder Lumr
Studio does, so the two plugins share one home. Both read the environment at
call time, never at import: tests change it after the modules load.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def lumr_home() -> Path:
    """The Lumr home folder: ``LUMR_HOME``, else the folder saved in ``~/.config/lumr/home.txt``, else ``~/Lumr``."""
    env = os.environ.get("LUMR_HOME")
    if env:
        return Path(env)
    saved = Path.home() / ".config" / "lumr" / "home.txt"
    if saved.exists():
        folder = saved.read_text().strip()
        if folder:
            return Path(folder)
    return Path.home() / "Lumr"


def probe_duration(path: Path) -> float:
    """Media duration in seconds via ffprobe; 0.0 on any failure."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0
