# SPDX-License-Identifier: AGPL-3.0-only
"""One-at-a-time background jobs with progress, so the window stays usable (decision Q14)."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

from .library import LibraryError


class Cancelled(Exception):
    """Raised inside a job when the person pressed Cancel."""


class Job:
    def __init__(self, kind: str, title: str, archive_id: str = ""):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.title = title
        self.archive_id = archive_id   # lets the window refresh the right page when the job ends
        self.state = "queued"          # queued | running | done | failed | cancelled
        self.progress = 0.0            # 0..1
        self.message = ""
        self.error = ""
        self.started = time.time()
        self._cancel = threading.Event()

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        """Called by the job's own thread. Also the place where Cancel takes effect."""
        if self._cancel.is_set():
            raise Cancelled()
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "archive_id": self.archive_id, "state": self.state,
                "progress": round(self.progress, 4), "message": self.message, "error": self.error}


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active: Job | None = None
        self._last: Job | None = None

    def start(self, kind: str, title: str, fn: Callable[[Job], None], archive_id: str = "") -> Job:
        with self._lock:
            if self._active is not None:
                raise LibraryError(f"Please wait: “{self._active.title}” is still running.", 409)
            job = Job(kind, title, archive_id)
            self._jobs[job.id] = job
            self._active = job
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _run(self, job: Job, fn: Callable[[Job], None]) -> None:
        job.state = "running"
        try:
            fn(job)
            job.progress = 1.0
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
            job.message = "Cancelled."
        except LibraryError as err:
            job.state, job.error = "failed", str(err)
        except Exception as err:  # keep the app alive whatever the model code does
            job.state, job.error = "failed", f"Something went wrong: {err}"
        finally:
            with self._lock:
                self._last = job
                if self._active is job:
                    self._active = None

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise LibraryError("That job was not found.", 404)
        return job

    def current(self) -> dict | None:
        """The running job, or the most recent one so the window can show how it ended."""
        job = self._active or self._last
        return job.public() if job else None

    def cancel(self, job_id: str) -> None:
        self.get(job_id)._cancel.set()
