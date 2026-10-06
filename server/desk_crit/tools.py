"""One plain function per tool.

Derived from Lumr Studio's tools.py, slimmed to the five tools Desk Crit has.
Each takes the tool's inputs, does the work through the domain modules, and
returns a short JSON-able dict. None of them know about MCP: server.py wraps
them, and tests call them directly. Collaborators that touch the outside world
(the speech model, the model download, the job registry, the footage reader)
are keyword parameters, so tests can pass fakes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from desk_crit import frames as screenshots
from desk_crit import models
from desk_crit.errors import StudioError
from desk_crit.jobs import JOBS, JobHandle, JobRegistry
from desk_crit.project import append_receipt, open_project, reserve_unique_path
from desk_crit.transcript import load_transcript, pack_transcript
from desk_crit.transcription import Transcriber, run_transcription, speech_transcriber, transcript_summary
from desk_crit.word_finder import check_phrases, find_places, pack_places

# job_status holds a call open at most this long. MCP clients give up on a
# tool call after about a minute.
MAX_WAIT_SECONDS = 50.0

# The kind of job that fetches the model.
DOWNLOAD_KIND = "download_models"

# What transcribe says when the model is missing: for Claude, to act on.
ASK_BEFORE_DOWNLOADING = (
    "Nothing has downloaded and nothing will until the creator says yes, so tell them what would "
    "download, how big it is, where from and under which license, and ask. Only when they say yes, "
    "call transcribe again with download_models true."
)


def _needs_model(model: models.Model) -> dict[str, Any]:
    """The ``needs_models`` answer."""
    described = model.describe()
    return {
        "status": "needs_models",
        "models": [described],
        "total_mb": described["size_mb"],
        "note": ASK_BEFORE_DOWNLOADING,
    }


def _start_download(project: Any, model: models.Model, opener: models.Opener, jobs: JobRegistry) -> dict[str, Any]:
    """Start the download of ``model``, or join the one already running for this recording."""

    def work(handle: JobHandle) -> dict[str, Any]:
        fetched = models.download([model], opener, handle.set_progress)
        return {"downloaded": [m.title for m in fetched]}

    job_id, _started = jobs.start_one(DOWNLOAD_KIND, project, work)
    return {"status": "downloading", "job_id": job_id}


def transcribe(
    video_path: str,
    force: bool = False,
    download_models: bool = False,
    *,
    transcriber: Transcriber = speech_transcriber,
    jobs: JobRegistry = JOBS,
    model: models.Model | None = None,
    opener: models.Opener = models.open_url,
) -> dict[str, Any]:
    """Start transcription, or report what already exists.

    Before any of that, the speech model may be missing:
    ``status: "needs_models"`` names it and starts nothing, and with
    ``download_models`` true a ``download_models`` job fetches it
    (``status: "downloading"``). A transcription already running for this
    recording is joined, not started twice.
    """
    project = open_project(video_path)
    model = models.SPEECH if model is None else model
    if project.words_path.exists() and not force:
        return {"status": "exists", **transcript_summary(project)}
    if not model.present():
        if download_models:
            return _start_download(project, model, opener, jobs)
        return _needs_model(model)
    job_id, _started = jobs.start_one("transcribe", project, lambda _h: run_transcription(project, force, transcriber))
    return {"job_id": job_id}


def read_transcript(video_path: str, start: float | None = None, end: float | None = None) -> dict[str, Any]:
    """Packed transcript text for a window, ending in ``NEXT <time>`` or ``END``."""
    project = open_project(video_path)
    words = load_transcript(project.words_path)
    return pack_transcript(words, start=start, end=end)


def find_words(video_path: str, words: list[str], start: float | None = None) -> dict[str, Any]:
    """Every place each of ``words`` is said, as packed text, for Claude to find what the creator pointed at.

    ``words`` holds one to five words or short phrases. Each place has an id,
    its clock time and the words around it. ``start`` goes on from a
    ``NEXT <time>`` line.
    """
    phrases = check_phrases(words)
    project = open_project(video_path)
    spoken = load_transcript(project.words_path)
    duration = project.duration()
    if start is not None and not 0 <= start <= duration:
        raise StudioError(f"start {start} is outside the recording (0 to {duration:.2f}).")
    return pack_places(find_places(spoken, phrases), phrases, start=start)


def frames(
    video_path: str,
    times: list[float],
    region: list[float] | None = None,
    *,
    footage: screenshots.ScreenFootage | None = None,
) -> dict[str, Any]:
    """Save a screenshot of the recording at each of ``times`` into ``frames/`` and describe them.

    Needs no transcript. Each picture is the frame on screen at that time,
    cropped to ``region`` when there is one. Nothing is written unless every
    frame was read.
    """
    project = open_project(video_path)
    shots, info, box = screenshots.capture(project.video, times, region, footage=footage)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    described: list[dict[str, Any]] = []
    reserved: list[Path] = []
    try:
        for shot in shots:
            cs = round(shot.at * 100)
            base = f"frame-{cs // 6000}m{(cs % 6000) / 100:05.2f}s-{stamp}"
            out = reserve_unique_path(project.frames_dir, base, shot.suffix)
            reserved.append(out)
            out.write_bytes(shot.data)
            described.append(shot.describe(out))
    except BaseException:
        for out in reserved:
            out.unlink(missing_ok=True)
        raise
    append_receipt(project, "frames", times=[s.at for s in shots], paths=[d["path"] for d in described])
    return {
        "frames": described,
        "source_size": [info.width, info.height],
        "region": list(box) if box else None,
    }


def job_status(job_id: str, wait: float = 0.0, *, jobs: JobRegistry = JOBS) -> dict[str, Any]:
    """Status, progress, result or error of a job.

    With ``wait`` the call holds until the job finishes or ``wait`` seconds pass.
    """
    if not 0 <= wait <= MAX_WAIT_SECONDS:
        raise StudioError(f"wait {wait} must be between 0 and {MAX_WAIT_SECONDS:.0f} seconds.")
    if wait:
        return jobs.wait(job_id, timeout=wait)
    return jobs.status(job_id)
