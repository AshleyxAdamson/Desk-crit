import json
import threading

import pytest

from desk_crit.errors import StudioError
from desk_crit.project import open_project


def _receipts(project):
    return [json.loads(line) for line in project.receipts_path.read_text().splitlines()]


def test_job_that_succeeds(jobs, video):
    project = open_project(str(video))

    def work(handle):
        handle.set_progress(0.5)
        return {"answer": 42}

    job_id = jobs.start("demo", project, work)
    status = jobs.wait(job_id, timeout=5)
    assert status["status"] == "done"
    assert status["progress"] == 1.0
    assert status["result"] == {"answer": 42}
    assert status["error"] is None
    (receipt,) = _receipts(project)
    assert receipt["step"] == "demo" and receipt["status"] == "done" and receipt["job_id"] == job_id


def test_job_that_fails_with_a_studio_error(jobs, video):
    project = open_project(str(video))

    def work(_handle):
        raise StudioError("The thing broke. Do the other thing.")

    status = jobs.wait(jobs.start("demo", project, work), timeout=5)
    assert status["status"] == "failed"
    assert status["error"] == "The thing broke. Do the other thing."
    assert _receipts(project)[0]["status"] == "failed"


def test_unexpected_crash_reports_no_traceback(jobs, video):
    project = open_project(str(video))

    def work(_handle):
        raise KeyError("start")

    status = jobs.wait(jobs.start("demo", project, work), timeout=5)
    assert status["status"] == "failed"
    assert status["error"].startswith("KeyError: 'start'")
    assert "Traceback" not in status["error"]


def test_unknown_job_id(jobs):
    with pytest.raises(StudioError, match="No job with id"):
        jobs.status("nope")


# ── one at a time ─────────────────────────────────────────────────────────────


class Held:
    """Work that waits until the test lets it finish."""

    def __init__(self):
        self.go = threading.Event()
        self.started = 0
        self._count = threading.Lock()

    def __call__(self, _handle):
        with self._count:
            self.started += 1
        assert self.go.wait(5), "the test never let the job finish"
        return {"ok": True}


@pytest.fixture
def held():
    work = Held()
    yield work
    work.go.set()


def test_start_one_starts_nothing_while_one_is_running(jobs, video, held):
    project = open_project(str(video))
    first, started = jobs.start_one("demo", project, held)
    again, started_again = jobs.start_one("demo", project, held)
    assert started and not started_again and again == first
    held.go.set()
    assert jobs.wait(first, timeout=5)["status"] == "done" and held.started == 1


def test_start_one_starts_a_new_job_once_the_last_one_finished(jobs, video, held):
    project = open_project(str(video))
    held.go.set()
    first, _ = jobs.start_one("demo", project, held)
    jobs.wait(first, timeout=5)
    second, started = jobs.start_one("demo", project, held)
    assert started and second != first


def test_a_job_started_the_plain_way_counts_as_running(jobs, video, held):
    project = open_project(str(video))
    plain = jobs.start("demo", project, held)
    assert jobs.start_one("demo", project, held) == (plain, False)


def test_another_kind_or_another_video_is_not_in_the_way(jobs, video, held, tmp_path):
    project = open_project(str(video))
    other_video = tmp_path / "other.mp4"
    other_video.write_bytes(video.read_bytes())
    jobs.start_one("demo", project, held)
    assert jobs.start_one("other", project, held)[1] is True
    assert jobs.start_one("demo", open_project(str(other_video)), held)[1] is True


def test_many_callers_at_once_start_exactly_one(jobs, video, held):
    project = open_project(str(video))
    answers = []
    gate = threading.Barrier(8)

    def caller():
        gate.wait(5)
        answers.append(jobs.start_one("demo", project, held))

    threads = [threading.Thread(target=caller) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(answers) == 8 and sum(1 for _, started in answers if started) == 1
    assert len({job_id for job_id, _ in answers}) == 1


def test_latest_is_the_running_job_else_the_newest_finished_one(jobs, video, held):
    project = open_project(str(video))
    assert jobs.latest("demo", project) is None
    done = jobs.start("demo", project, lambda _h: {"n": 1})
    jobs.wait(done, timeout=5)
    assert jobs.latest("demo", project).job_id == done
    running = jobs.start("demo", project, held)
    newer = jobs.start("demo", project, lambda _h: {"n": 2})
    jobs.wait(newer, timeout=5)
    assert jobs.latest("demo", project).job_id == running
    held.go.set()
    jobs.wait(running, timeout=5)
    assert jobs.latest("demo", project).job_id == newer
    assert jobs.latest("other", project) is None


def test_a_job_remembers_what_it_was_started_with(jobs, video, held):
    project = open_project(str(video))
    about = {"edit_mark": "abc123"}
    job_id, _ = jobs.start_one("demo", project, held, about=about)
    about["edit_mark"] = "changed by the caller later"
    assert jobs.get(job_id).about == {"edit_mark": "abc123"}
    assert jobs.get(jobs.start("demo", project, lambda _h: {})).about == {}
