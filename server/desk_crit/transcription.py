"""The work behind ``transcribe``: run the speech model, then check the result.

Derived from Lumr Studio's transcription.py, slimmed: no word times, because
Desk Crit cuts nothing. The speech entry (``python -m desk_crit.speech``)
writes ``<video>.words.json`` beside the recording. That sidecar is the only
file this server ever causes to appear in the recording's folder.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from desk_crit.errors import StudioError
from desk_crit.project import Project, words_path_for
from desk_crit.transcript import load_transcript, spoken_words

# (video, force) -> None; produces the words.json sidecar or raises.
Transcriber = Callable[[Path, bool], None]


def run_speech_model(video: Path) -> subprocess.CompletedProcess[str]:
    """Run the speech entry on ``video`` in its own process and wait for it.

    A process of its own gives the model's memory back when it exits, and a
    crash in the speech stack can't take the server down. The model is read
    from the folder ``models.download`` filled, and Hugging Face's library is
    told to stay offline: no download happens here, whatever is missing.
    """
    return subprocess.run(
        [sys.executable, "-m", "desk_crit.speech", str(video)],
        env={**os.environ, "HF_HUB_OFFLINE": "1"},
        stdin=subprocess.DEVNULL,  # the server's own stdin is the protocol stream
        capture_output=True,
        text=True,
    )


def speech_transcriber(video: Path, force: bool) -> None:
    """The production transcriber: the speech entry, which writes the words.json sidecar."""
    if not force and words_path_for(video).exists():
        return
    done = run_speech_model(video)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        raise StudioError(
            f"The speech model could not transcribe {video.name}: {detail[-600:]} "
            "Check the video plays, then run transcribe again."
        )


def transcript_summary(project: Project) -> dict[str, Any]:
    """``{words_path, word_count, duration}`` for an existing transcript."""
    words = load_transcript(project.words_path)
    return {
        "words_path": str(project.words_path),
        "word_count": len(spoken_words(words)),
        "duration": round(project.duration(), 3),
    }


def run_transcription(project: Project, force: bool, transcriber: Transcriber) -> dict[str, Any]:
    """Transcribe, then validate the sidecar the speech model wrote."""
    transcriber(project.video, force)
    if not project.words_path.exists():
        raise StudioError(
            f"Transcription finished but no transcript appeared at {project.words_path}. "
            "Run transcribe again with force=true."
        )
    return transcript_summary(project)
