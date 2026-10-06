"""Shared fixtures. Every test runs against temp folders, never the user's LUMR_HOME."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from desk_crit import models, transcription
from desk_crit.jobs import JobRegistry

VIDEO_SECONDS = 20.0

# (word, start, end[, "event"]). A 20 s talk with pauses, a filler, an event,
# and a repeat ("today we talk about editing" said twice).
WORD_ROWS = [
    ("Hello", 0.50, 0.90), ("everyone", 0.95, 1.50), ("and", 1.60, 1.80), ("welcome.", 1.85, 2.40),
    ("um", 3.40, 3.70), ("today", 3.90, 4.30), ("we", 4.35, 4.50), ("talk", 4.55, 4.90),
    ("about", 4.95, 5.30), ("editing", 5.35, 6.00), ("[vocalization]", 6.00, 6.60, "event"),
    ("today", 8.00, 8.40), ("we", 8.45, 8.60), ("talk", 8.65, 9.00), ("about", 9.05, 9.40),
    ("editing", 9.45, 10.10), ("video.", 10.15, 10.70),
    ("this", 12.00, 12.30), ("is", 12.35, 12.50), ("the", 12.55, 12.70), ("part", 12.75, 13.10),
    ("that", 13.15, 13.40), ("matters.", 13.45, 14.20),
    ("thanks", 15.50, 16.00), ("for", 16.05, 16.20), ("watching.", 16.25, 17.00),
]


def make_words() -> list[dict]:
    words = []
    for row in WORD_ROWS:
        entry = {"word": row[0], "start": row[1], "end": row[2], "energy_rms": 0.1}
        if len(row) == 4:
            entry["type"] = row[3]
        words.append(entry)
    return words


def spread_evenly(sentences: list[tuple[str, float, float]]) -> list[dict]:
    """Words with SYNTHESIZED timings: each sentence's words share its span equally.

    Reproduces the Hammy bug: every word in a sentence gets the same duration,
    and neighbouring sentences may overlap.
    """
    words = []
    for text, start, end in sentences:
        tokens = text.split()
        step = (end - start) / len(tokens)
        words += [
            {"word": t, "start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3)}
            for i, t in enumerate(tokens)
        ]
    return sorted(words, key=lambda w: w["start"])


def spoken_run(text: str, start: float, step: float = 0.4, gap: float = 0.05) -> list[dict]:
    """Measured-looking words back to back: durations vary, gaps under a pause."""
    words = []
    t = start
    for i, token in enumerate(text.split()):
        dur = step - gap + (i % 3) * 0.03
        words.append({"word": token, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + gap
    return words


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point every root at temp folders and forbid the speech model.

    No test runs the speech model: a test that transcribes passes a fake
    transcriber. No test downloads the model: ``transcribe`` is handed a tiny
    stand-in (``model=``, with a fake ``opener=``) when a test needs one.
    """
    monkeypatch.setenv("DESK_CRIT_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("LUMR_HOME", str(tmp_path / "lumr-home"))
    # The model caches are temp folders too: a test never reads or fills the ones on this machine.
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch-home"))

    def forbidden(*_a, **_k):
        raise AssertionError("tests must not run the speech model")

    monkeypatch.setattr(transcription, "run_speech_model", forbidden)
    # The real model is in the (empty) temp cache, so a test that wants it present swaps in ``fake_speech``.
    assert models.SPEECH.folder.is_relative_to(tmp_path)
    return tmp_path


@pytest.fixture
def words() -> list[dict]:
    return make_words()


@pytest.fixture
def jobs() -> JobRegistry:
    return JobRegistry()


@pytest.fixture(scope="session")
def synthetic_master(tmp_path_factory) -> Path:
    """A 20 s test-pattern video with a sine tone, generated once per session."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("master") / "talk.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={VIDEO_SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={VIDEO_SECONDS}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out),
        ],
        check=True,
    )
    return out


@pytest.fixture
def video(tmp_path, synthetic_master) -> Path:
    """A fresh copy of the synthetic video with its words.json sidecar beside it."""
    folder = tmp_path / "footage"
    folder.mkdir()
    path = folder / "talk.mp4"
    shutil.copy(synthetic_master, path)
    (folder / "talk.words.json").write_text(json.dumps(make_words()))
    return path


@pytest.fixture
def speech(tmp_path, monkeypatch):
    """``models.SPEECH`` swapped for a tiny model in a temp folder, not yet downloaded.

    Only a test that asks for it gets it. ``tools.transcribe`` and the speech
    entry both read ``models.SPEECH`` when they run, so they see this one.
    """
    from tiny_models import tiny_speech

    model = tiny_speech(tmp_path / "models" / "speech")
    monkeypatch.setattr(models, "SPEECH", model)
    return model
