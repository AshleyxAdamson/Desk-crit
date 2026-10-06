"""``transcribe`` and the model: it asks before anything downloads, then fetches on a yes (fake network only).

The model is a tiny stand-in in a temp folder (``tiny_models``), the transcriber
is a fake, and the network is ``FakeNet``. No test loads a model or reaches
the internet.
"""

from __future__ import annotations

import json
import threading

import pytest
from tiny_models import FakeNet, content_of

from desk_crit import models, tools
from desk_crit.project import open_project


@pytest.fixture
def net(speech):
    return FakeNet(content_of(speech))


@pytest.fixture
def no_transcript(video):
    video.with_suffix(".words.json").unlink()
    return video


def fake_transcriber(words):
    def transcribe(path, force):
        path.with_suffix(".words.json").write_text(json.dumps(words))

    return transcribe


def ask(video, jobs, net, **more):
    return tools.transcribe(str(video), jobs=jobs, opener=net, **more)


# ── the question ──────────────────────────────────────────────────────────────


def test_with_no_transcript_and_no_model_it_names_the_model_and_downloads_nothing(no_transcript, jobs, speech, net):
    answer = ask(no_transcript, jobs, net)
    assert answer["status"] == "needs_models"
    assert [m["name"] for m in answer["models"]] == ["Tiny speech model"]
    assert [(m["source_host"], m["license"]) for m in answer["models"]] == [("hub.example.test", "CC-BY-4.0")]
    assert all(set(m) == {"name", "size_mb", "source_host", "license", "folder"} for m in answer["models"])
    assert answer["total_mb"] == answer["models"][0]["size_mb"]
    assert "ask" in answer["note"] and "download_models" in answer["note"] and "Nothing has downloaded" in answer["note"]
    assert "job_id" not in answer
    assert net.requests == [], "no byte moved"
    assert not speech.folder.exists(), "no folder made either"
    assert jobs.latest("download_models", open_project(str(no_transcript))) is None


def test_the_answer_a_stranger_gets_carries_the_real_size_host_and_license(no_transcript, jobs):
    """The real catalog, an empty cache, and a network that must not be touched."""
    def forbidden(url, start):
        raise AssertionError("asking must not download")

    answer = tools.transcribe(str(no_transcript), jobs=jobs, opener=forbidden)
    (speech,) = answer["models"]
    assert "Parakeet" in speech["name"] and (speech["size_mb"], speech["source_host"], speech["license"]) == (
        2472, "huggingface.co", "CC-BY-4.0")
    assert answer["total_mb"] == 2472
    assert speech["folder"].endswith(f"snapshots/{models.SPEECH_REVISION}")
    assert not models.SPEECH.folder.exists(), "asking made no folder"


def test_the_real_model_is_pinned_to_the_same_files_lumr_studio_uses():
    assert models.SPEECH_REPO == "mlx-community/parakeet-tdt-0.6b-v2"
    assert models.SPEECH_REVISION == "8ae155301e23d820d82aa60d24817c900e69e487"
    assert [(f.name, f.size) for f in models.SPEECH.files] == [("config.json", 36_176), ("model.safetensors", 2_471_559_904)]
    assert all(f.url.startswith(f"https://huggingface.co/{models.SPEECH_REPO}/resolve/{models.SPEECH_REVISION}/")
               for f in models.SPEECH.files)
    assert all(len(f.sha256) == 64 for f in models.SPEECH.files)
    assert models.USER_AGENT == "desk-crit-plugin"


def test_forcing_a_new_transcript_needs_the_model_too(video, jobs, speech, net):
    answer = ask(video, jobs, net, force=True)
    assert answer["status"] == "needs_models" and net.requests == []


def test_a_model_already_there_is_not_asked_for_again(no_transcript, jobs, speech, net, words):
    models.download([speech], net)
    again = ask(no_transcript, jobs, net, transcriber=fake_transcriber(words))
    assert set(again) == {"job_id"}


# ── the yes ───────────────────────────────────────────────────────────────────


def test_after_a_yes_the_job_downloads_shows_progress_and_the_next_call_starts_transcribing(
    no_transcript, jobs, speech, net, words,
):
    started = ask(no_transcript, jobs, net, download_models=True)
    assert set(started) == {"status", "job_id"}
    assert started["status"] == "downloading" and started["job_id"].startswith("download_models-")
    done = jobs.wait(started["job_id"], timeout=10)
    assert done["status"] == "done", done
    assert done["progress"] == 1.0 and done["kind"] == "download_models"
    assert done["result"] == {"downloaded": ["Tiny speech model"]}
    assert speech.present()
    # The same call now goes on: it transcribes, with nothing left to ask.
    again = ask(no_transcript, jobs, net, transcriber=fake_transcriber(words))
    assert set(again) == {"job_id"} and again["job_id"].startswith("transcribe-")
    assert jobs.wait(again["job_id"], timeout=10)["result"]["word_count"] == 25


def test_progress_reads_between_zero_and_one_while_the_files_arrive(no_transcript, jobs, speech):
    started_reading, release = threading.Event(), threading.Event()

    class Held(FakeNet):
        def _chunks(self, data, size=1000):
            for i, chunk in enumerate(super()._chunks(data, size)):
                if i == 3:
                    started_reading.set()
                    release.wait(5)
                yield chunk

    held = Held(content_of(speech))
    started = ask(no_transcript, jobs, held, download_models=True)
    assert started_reading.wait(5)
    running = tools.job_status(started["job_id"], jobs=jobs)
    assert running["status"] == "running" and 0.0 < running["progress"] < 1.0
    release.set()
    assert jobs.wait(started["job_id"], timeout=10)["progress"] == 1.0


def test_asking_again_for_a_download_that_is_running_joins_it(no_transcript, jobs, speech):
    started_reading, release = threading.Event(), threading.Event()

    class Held(FakeNet):
        def _chunks(self, data, size=1000):
            started_reading.set()
            release.wait(5)
            yield from super()._chunks(data, size)

    held = Held(content_of(speech))
    first = ask(no_transcript, jobs, held, download_models=True)
    assert started_reading.wait(5)
    second = ask(no_transcript, jobs, held, download_models=True)
    release.set()
    assert second["status"] == "downloading" and second["job_id"] == first["job_id"]
    assert jobs.wait(first["job_id"], timeout=10)["status"] == "done"


def test_a_yes_when_the_model_is_there_downloads_nothing_and_goes_on(no_transcript, jobs, speech, net, words):
    models.download([speech], net)
    before = list(net.requests)
    answer = ask(no_transcript, jobs, net, download_models=True, transcriber=fake_transcriber(words))
    assert set(answer) == {"job_id"} and answer["job_id"].startswith("transcribe-") and net.requests == before


# ── when it goes wrong ────────────────────────────────────────────────────────


def test_a_bad_checksum_fails_the_job_with_the_reason_and_installs_nothing(no_transcript, jobs, speech, net):
    url = speech.files[1].url
    corrupt = FakeNet({**net.served, url: bytes(reversed(net.served[url]))})
    started = ask(no_transcript, jobs, corrupt, download_models=True)
    done = jobs.wait(started["job_id"], timeout=10)
    assert done["status"] == "failed"
    assert "didn't match its checksum" in done["error"] and "Tiny speech model" in done["error"]
    assert not speech.present()
    assert not list(speech.folder.glob("*.part")), "the partial file is gone"
    # Tried again on a good network, it lands.
    again = ask(no_transcript, jobs, net, download_models=True)
    assert jobs.wait(again["job_id"], timeout=10)["status"] == "done" and speech.present()


def test_a_download_that_stops_fails_the_job_and_the_next_yes_resumes_it(no_transcript, jobs, speech, net):
    cut = FakeNet(net.served, cut_after=3000)
    started = ask(no_transcript, jobs, cut, download_models=True)
    done = jobs.wait(started["job_id"], timeout=10)
    assert done["status"] == "failed" and "picks up where it left off" in done["error"]
    resumed = FakeNet(net.served)
    again = ask(no_transcript, jobs, resumed, download_models=True)
    assert jobs.wait(again["job_id"], timeout=10)["status"] == "done"
    assert (resumed.requests[0][1], speech.present()) == (3000, True)


def test_the_download_is_written_to_the_receipts_of_the_video(no_transcript, jobs, speech, net):
    started = ask(no_transcript, jobs, net, download_models=True)
    jobs.wait(started["job_id"], timeout=10)
    receipts = open_project(str(no_transcript)).root / "receipts.jsonl"
    lines = [json.loads(line) for line in receipts.read_text().splitlines()]
    assert [(line["step"], line["status"]) for line in lines] == [("download_models", "done")]
