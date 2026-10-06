"""The per-recording project folder: where it lives, what's in it, and how it's written.

Derived from Lumr Studio's project.py, slimmed: no edit, exports, publish kit
or looks. Layout:

    <projects root>/<video stem>-<first 12 hex of sha1(resolved path)>/
        receipts.jsonl  frames/

The transcript is not in the project folder. It is the sidecar
``<video>.words.json`` beside the recording, the same file Lumr Studio writes
and reads, so a recording transcribed by either plugin is never transcribed
twice.

The projects root is resolved at CALL time, never at import: tests change the
environment after the modules load.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from desk_crit.errors import StudioError
from desk_crit.paths import lumr_home, probe_duration

PROJECTS_DIR_ENV = "DESK_CRIT_PROJECTS_DIR"
TRANSCRIPT_SUFFIX = ".words.json"


def projects_root() -> Path:
    """The folder that holds every project. The env override wins over LUMR_HOME."""
    override = os.environ.get(PROJECTS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return lumr_home() / "desk-crit" / "projects"


def resolve_video(video_path: str) -> Path:
    """Check ``video_path`` names an existing file and return it resolved.

    Raises StudioError when the path is relative, missing, or not a file.
    """
    if not isinstance(video_path, str) or not video_path.strip():
        raise StudioError("video_path is empty. Pass the absolute path to the recording.")
    raw = Path(video_path).expanduser()
    if not raw.is_absolute():
        raise StudioError(
            f"video_path {video_path!r} is relative. Pass the absolute path to the recording."
        )
    if not raw.exists():
        raise StudioError(f"No file at {raw}. Check the path and pass the absolute path to the recording.")
    if not raw.is_file():
        raise StudioError(f"{raw} is a directory, not a video file. Pass the path to the recording itself.")
    return raw.resolve()


def words_path_for(video: Path) -> Path:
    """Where the speech model writes the word-timing sidecar: beside the video, same stem."""
    return video.with_suffix(TRANSCRIPT_SUFFIX)


@dataclass(frozen=True)
class Project:
    """One recording's project folder. Construct with ``open_project``."""

    video: Path
    root: Path

    @property
    def receipts_path(self) -> Path:
        return self.root / "receipts.jsonl"

    @property
    def frames_dir(self) -> Path:
        return self.root / "frames"

    @property
    def words_path(self) -> Path:
        return words_path_for(self.video)

    def duration(self) -> float:
        """Recording length in seconds, probed with ffprobe.

        Raises StudioError when ffprobe cannot read a duration.
        """
        seconds = probe_duration(self.video)
        if seconds <= 0:
            raise StudioError(
                f"ffprobe could not read a duration from {self.video}. "
                "Check that it is a playable video file and that ffprobe is installed."
            )
        return seconds


def project_dir_name(video: Path) -> str:
    """``<stem>-<first 12 hex of sha1(resolved path)>``."""
    digest = hashlib.sha1(str(video).encode("utf-8")).hexdigest()[:12]
    return f"{video.stem}-{digest}"


def open_project(video_path: str) -> Project:
    """Validate the video path and return its project, creating the project folder on first use.

    Only the project folder is made. ``frames/`` appears when the first
    screenshot is saved.
    """
    video = resolve_video(video_path)
    root = projects_root() / project_dir_name(video)
    root.mkdir(parents=True, exist_ok=True)
    return Project(video=video, root=root)


def now_iso() -> str:
    """UTC timestamp for receipts and saved files."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file in the same folder, then replace.

    A reader never sees a half-written file: it sees the old one or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, data: Any) -> None:
    """``write_text_atomic`` for JSON (indented, UTF-8, trailing newline)."""
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def append_receipt(project: Project, step: str, **fields: Any) -> None:
    """Append one line to ``receipts.jsonl`` recording a completed step."""
    record = {"at": now_iso(), "step": step, **fields}
    with open(project.receipts_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def reserve_unique_path(folder: Path, base: str, suffix: str) -> Path:
    """Claim a new file in ``folder`` named ``base`` + ``suffix`` and return its path.

    Adds ``-2``, ``-3``… on a clash, so an existing file is never overwritten.
    The file is created empty with exclusive-create, so two jobs started in the
    same second cannot pick the same name.
    """
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        name = f"{base}{suffix}" if n == 1 else f"{base}-{n}{suffix}"
        candidate = folder / name
        try:
            with open(candidate, "x"):
                return candidate
        except FileExistsError:
            n += 1
