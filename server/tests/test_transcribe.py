import json
import subprocess
import sys

import pytest

from desk_crit import tools, transcription
from desk_crit.errors import StudioError
from desk_crit.transcription import run_speech_model, speech_transcriber

# The fixture forbids ``transcription.run_speech_model``; keep the real one from before it did.
real_run_speech_model = run_speech_model


def test_existing_transcript_is_reported_not_redone(video, jobs):
    result = tools.transcribe(str(video), jobs=jobs)
    assert result == {
        "status": "exists",
        "words_path": str(video.with_suffix(".words.json")),
        "word_count": 25,
        "duration": pytest.approx(20.0, abs=0.1),
    }


def test_an_existing_transcript_needs_no_model(video, jobs, speech):
    assert not speech.present()
    assert tools.transcribe(str(video), jobs=jobs)["status"] == "exists"


def test_transcription_job_validates_what_the_speech_model_wrote(video, jobs, words, speech):
    sidecar = video.with_suffix(".words.json")
    sidecar.unlink()
    speech_files(speech)

    def fake_transcriber(path, force):
        path.with_suffix(".words.json").write_text(json.dumps(words))

    job = tools.transcribe(str(video), transcriber=fake_transcriber, jobs=jobs)
    status = jobs.wait(job["job_id"], timeout=10)
    assert status["status"] == "done", status
    assert status["result"]["word_count"] == 25
    assert set(status["result"]) == {"words_path", "word_count", "duration"}


def test_transcription_job_that_writes_nothing_fails(video, jobs, speech):
    video.with_suffix(".words.json").unlink()
    speech_files(speech)
    job = tools.transcribe(str(video), transcriber=lambda _p, _f: None, jobs=jobs)
    status = jobs.wait(job["job_id"], timeout=10)
    assert status["status"] == "failed"
    assert "no transcript appeared" in status["error"]


def test_a_second_call_while_the_transcription_runs_joins_it(video, jobs, speech, words):
    import threading

    video.with_suffix(".words.json").unlink()
    speech_files(speech)
    go = threading.Event()

    def slow(path, force):
        assert go.wait(5)
        path.with_suffix(".words.json").write_text(json.dumps(words))

    first = tools.transcribe(str(video), transcriber=slow, jobs=jobs)
    second = tools.transcribe(str(video), force=True, transcriber=slow, jobs=jobs)
    go.set()
    assert second["job_id"] == first["job_id"]
    assert jobs.wait(first["job_id"], timeout=10)["status"] == "done"


def speech_files(model):
    """Put the tiny speech model on disk the way a consented download does."""
    from tiny_models import FakeNet, content_of

    from desk_crit import models

    models.download([model], FakeNet(content_of(model)))


def test_relative_and_missing_paths(tmp_path):
    for call in (tools.transcribe, tools.read_transcript, tools.frames):
        args = ([1.0],) if call is tools.frames else ()
        with pytest.raises(StudioError, match="relative"):
            call("clip.mp4", *args)
        with pytest.raises(StudioError, match="No file at"):
            call(str(tmp_path / "missing.mp4"), *args)
    with pytest.raises(StudioError, match="relative"):
        tools.find_words("clip.mp4", ["this"])


def done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_the_speech_model_runs_in_a_process_of_its_own_that_cannot_read_the_servers_input(monkeypatch, video):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        return done(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    real_run_speech_model(video)
    assert seen["cmd"] == [sys.executable, "-m", "desk_crit.speech", str(video)]
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL  # the server's own stdin is the protocol stream
    assert seen["kwargs"]["env"]["HF_HUB_OFFLINE"] == "1"  # nothing is looked up online


def test_the_transcriber_runs_the_speech_model_and_stops_when_it_succeeds(monkeypatch, video):
    video.with_suffix(".words.json").unlink()
    ran = []
    monkeypatch.setattr(transcription, "run_speech_model", lambda v: ran.append(v) or done(0))
    speech_transcriber(video, False)
    assert ran == [video]


def test_a_failed_speech_model_says_what_it_said(monkeypatch, video):
    video.with_suffix(".words.json").unlink()
    monkeypatch.setattr(transcription, "run_speech_model", lambda v: done(1, stderr="no such model\n" * 100))
    with pytest.raises(StudioError, match="(?s)could not transcribe talk.mp4: .*no such model") as raised:
        speech_transcriber(video, False)
    assert len(str(raised.value)) < 900  # only the tail of what it said


def test_a_transcript_that_is_there_is_left_alone_unless_forced(monkeypatch, video):
    ran = []
    monkeypatch.setattr(transcription, "run_speech_model", lambda v: ran.append(v) or done(0))
    speech_transcriber(video, False)
    assert ran == []
    speech_transcriber(video, True)
    assert ran == [video]


def test_the_speech_entry_refuses_with_a_plain_line_when_the_model_is_missing(video, speech, capsys):
    from desk_crit.speech.__main__ import NO_MODEL, main

    assert main([str(video)]) == 3
    assert NO_MODEL in capsys.readouterr().err
    assert main([]) == 2
