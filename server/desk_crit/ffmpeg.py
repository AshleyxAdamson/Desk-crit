"""Running ffmpeg and ffprobe, and the numbers the screenshots share.

Derived from Lumr Studio's look.py and edit.py, slimmed. Desk Crit only reads
frames out of a recording, so this holds the one runner, the timeout, the size
limits for a screenshot, and the ``m:ss`` clock. Nothing here is a copy that
``tools/sync-from-lumr.sh`` writes: keep it in step by hand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from desk_crit.errors import StudioError

# One ffmpeg or ffprobe command may run this long before it is stopped.
FFMPEG_TIMEOUT_SECONDS = 60.0
# A screenshot's long side, in pixels. Larger frames are scaled down to this.
MAX_LONG_EDGE = 1568
# A screenshot's file stays under this many bytes.
MAX_FILE_BYTES = 1_000_000


def _run(cmd: list[str], *, what: str, video: Path, log: Path | None = None,
         capture_stdout: bool = False) -> str:
    """Run one ffmpeg or ffprobe command to completion and return its stdout.

    stderr goes to ``log`` (a file, never a pipe that could fill) or is
    captured by ``communicate`` for ffprobe's short output. Raises StudioError
    with the tail of stderr when the command fails, hangs, or is missing.
    """
    try:
        if log is not None:
            with open(log, "wb") as err:
                done = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=err, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
            stderr = log.read_bytes()
            stdout = b""
        else:
            done = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
            stderr = done.stderr or b""
            stdout = done.stdout or b""
    except FileNotFoundError:
        raise StudioError(f"{cmd[0]} is not installed or not on PATH. Install ffmpeg and try again.") from None
    except subprocess.TimeoutExpired:
        raise StudioError(
            f"{what} {video.name}: it ran past {FFMPEG_TIMEOUT_SECONDS:.0f}s. "
            "Check that the video plays and is on a local disk."
        ) from None
    if done.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip()[-400:]
        raise StudioError(f"{what} {video.name}: {tail} Check that the file is a playable video.")
    return stdout.decode("utf-8", "replace")


def clock(seconds: float) -> str:
    """``m:ss`` for a time in seconds, rounded down to the whole second.

    240.4 is ``4:00``. The model gets this ready-made: asked to format a time
    by hand it has written ``3:60``.
    """
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"
