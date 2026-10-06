# COPIED by tools/sync-from-lumr.sh from lumr-studio/server/lumr_studio/jobs.py. Do not edit: change it in Lumr Studio and run the script.
"""Background jobs: long work runs on a thread and is polled by ``job_status``.

Job state lives in memory, so it is gone when the server restarts. Every
finished job also appends a receipt line to its project's ``receipts.jsonl``,
which survives.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from desk_crit.errors import StudioError
from desk_crit.project import Project, append_receipt, now_iso

logger = logging.getLogger(__name__)


class JobHandle:
    """What a running job uses to report progress."""

    def __init__(self) -> None:
        self._progress: float | None = None
        self._source: Callable[[], float] | None = None

    def set_progress(self, fraction: float) -> None:
        self._progress = max(0.0, min(1.0, fraction))

    def track(self, source: Callable[[], float]) -> None:
        """Read progress from ``source`` whenever status is asked for."""
        self._source = source

    def progress(self) -> float | None:
        if self._source is not None:
            return max(0.0, min(1.0, float(self._source())))
        return self._progress


Work = Callable[[JobHandle], dict[str, Any]]


@dataclass
class Job:
    job_id: str
    kind: str
    project: Project
    # Facts about what the job was started with, for whoever reads it later
    # (the page's export status reads which edit a render holds).
    about: dict[str, Any] = field(default_factory=dict)
    handle: JobHandle = field(default_factory=JobHandle)
    status: str = "running"
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        progress = 1.0 if self.status == "done" else self.handle.progress()
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "progress": None if progress is None else round(progress, 3),
            "result": self.result,
            "error": self.error,
        }


def describe_failure(exc: BaseException) -> str:
    """The message the model sees for a failed job: never a traceback."""
    if isinstance(exc, StudioError):
        return str(exc)
    return (
        f"{type(exc).__name__}: {exc}. This is an internal error; "
        "the server log on stderr has the details."
    )


class JobRegistry:
    """Starts jobs on daemon threads and answers status questions about them."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, project: Project, work: Work, *, about: dict[str, Any] | None = None) -> str:
        """Run ``work`` in the background and return its job id at once."""
        job = self._new(kind, project, about)
        with self._lock:
            self._jobs[job.job_id] = job
        self._launch(job, work)
        return job.job_id

    def start_one(
        self, kind: str, project: Project, work: Work, *, about: dict[str, Any] | None = None
    ) -> tuple[str, bool]:
        """Start ``work`` unless a job of this ``kind`` is already running for ``project``.

        Returns ``(job_id, started)``: the new job and True, or the running
        job and False. The check and the start happen under one lock, so two
        callers at the same moment can never both start one.
        """
        with self._lock:
            running = self._latest(kind, project, running_only=True)
            if running is not None:
                return running.job_id, False
            job = self._new(kind, project, about)
            self._jobs[job.job_id] = job
        self._launch(job, work)
        return job.job_id, True

    def latest(self, kind: str, project: Project) -> Job | None:
        """The job of this ``kind`` started last for ``project``, or None. A running one wins over a finished one."""
        with self._lock:
            return self._latest(kind, project, running_only=True) or self._latest(kind, project)

    def _latest(self, kind: str, project: Project, *, running_only: bool = False) -> Job | None:
        """The newest matching job. Call with the lock held; jobs are kept in the order they started."""
        for job in reversed(self._jobs.values()):
            if job.kind == kind and job.project.root == project.root and (job.status == "running" or not running_only):
                return job
        return None

    @staticmethod
    def _new(kind: str, project: Project, about: dict[str, Any] | None) -> Job:
        return Job(job_id=f"{kind}-{uuid.uuid4().hex[:12]}", kind=kind, project=project, about=dict(about or {}))

    def _launch(self, job: Job, work: Work) -> None:
        threading.Thread(target=self._run, args=(job, work), name=job.job_id, daemon=True).start()

    def _run(self, job: Job, work: Work) -> None:
        try:
            job.result = work(job.handle)
            job.status = "done"
        except Exception as exc:
            if not isinstance(exc, StudioError):
                logger.exception("job %s failed", job.job_id)
            job.error = describe_failure(exc)
            job.status = "failed"
        job.finished_at = now_iso()
        try:
            append_receipt(
                job.project, job.kind, job_id=job.job_id, status=job.status,
                started_at=job.started_at, result=job.result, error=job.error,
            )
        except OSError:
            logger.exception("could not write receipt for job %s", job.job_id)
        job.done.set()

    def get(self, job_id: str) -> Job:
        """The job with this id. Raises StudioError for an unknown id."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise StudioError(
                f"No job with id {job_id!r}. Job ids come from transcribe; "
                "jobs are kept in memory, so a server restart forgets them. Start the job again."
            )
        return job

    def status(self, job_id: str) -> dict[str, Any]:
        return self.get(job_id).to_dict()

    def wait(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Block until the job finishes (or ``timeout`` passes) and return its status."""
        job = self.get(job_id)
        job.done.wait(timeout)
        return job.to_dict()


JOBS = JobRegistry()
