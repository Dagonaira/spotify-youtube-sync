"""Background job orchestration for the GUI.

Supports multiple queued sync jobs, but only ever runs ONE worker thread at a
time (concurrent workers would just compete for the same pooled daily quota
anyway). Live state lives in memory (never read from the progress file -
that's each job's own on-disk checkpoint); a background scheduler thread
wakes up due quota/rate-limit waits and advances the queue automatically.
"""

import dataclasses
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import spotify_to_youtube as s2y
import youtube_to_spotify as y2s
from common import get_data_dir, get_spotify_client, get_youtube_client, load_progress, save_progress
from sync_core import ErrorKind, LoopControl, run_sync_loop

PROJECT_DIR = get_data_dir()
YOUTUBE_TZ = ZoneInfo("America/Los_Angeles")
SCHEDULER_TICK_SECONDS = 60
WORKER_STOP_JOIN_TIMEOUT = 10


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_QUOTA = "waiting_quota"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobState:
    id: str
    status: JobStatus
    direction: str
    name: str
    source: str = None  # original playlist URL/ID or "liked" - kept so a
    # network failure before any progress file exists can still be retried
    progress_path: str = None
    total: int = 0
    done: int = 0
    added: int = 0
    not_found: int = 0
    resume_at: str = None
    wait_reason: str = None  # "quota" | "rate_limit", only meaningful while WAITING_QUOTA
    wait_detail: str = None  # underlying error text - diagnostic only, not shown as an "error" in the UI
    error_message: str = None
    result_playlist_url: str = None
    recent: list = field(default_factory=list)  # most-recent-first, capped at 25
    created_at: str = field(default_factory=lambda: datetime.now(tz=YOUTUBE_TZ).isoformat())


def _next_youtube_quota_reset() -> datetime:
    now = datetime.now(tz=YOUTUBE_TZ)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


class JobManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobState] = {}
        self._order: list[str] = []  # queue/display order (insertion order)
        self._active_job_id: str | None = None
        self._control: LoopControl | None = None
        self._worker_thread: threading.Thread | None = None
        self._pending_next: str | None = None
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_stop = threading.Event()

    # ---- public API, used by server.py ----

    def list_jobs(self) -> list:
        with self._lock:
            return [dataclasses.asdict(self._jobs[jid]) for jid in self._order]

    def get_job(self, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
            return dataclasses.asdict(job) if job else None

    def add_job(self, direction: str, source: str, name: str) -> dict:
        job_id = uuid.uuid4().hex[:12]
        state = JobState(id=job_id, status=JobStatus.QUEUED, direction=direction, name=name, source=source)
        with self._lock:
            self._jobs[job_id] = state
            self._order.append(job_id)
        self._advance_queue()
        return self.get_job(job_id)

    def pause(self, job_id: str = None) -> dict:
        """Pause a specific job, or whichever one is currently active if
        job_id is omitted. Whatever's next in the queue (if anything) picks
        up automatically - see _advance_queue.
        """
        with self._lock:
            target = job_id or self._active_job_id
            if not target or target not in self._jobs:
                return None
            is_active = target == self._active_job_id
            control = self._control if is_active else None
            worker = self._worker_thread if is_active else None
        if control:
            control.request_stop()
            if worker:
                worker.join(timeout=WORKER_STOP_JOIN_TIMEOUT)
        with self._lock:
            job = self._jobs.get(target)
            if job and job.status in (JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.WAITING_QUOTA):
                job.status = JobStatus.PAUSED
                job.resume_at = None
        self._advance_queue()
        return self.get_job(target)

    def resume(self, job_id: str) -> dict:
        """Make this job the active one, right now - pausing whatever else
        is currently running first. This is also how "switch to a different
        playlist" works: pause isn't required as a separate step.

        Stopping the current worker and starting job_id aren't one atomic
        step (the worker takes a moment to actually exit), and that worker's
        own finish-up code independently tries to advance the queue too. To
        avoid a race where both this call and that cleanup try to launch a
        job at once (or the wrong one wins), the requested job_id is staged
        as _pending_next before releasing anything - whichever path reaches
        _advance_queue first claims it, and _launch_worker's own guard makes
        a double-launch impossible either way.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status == JobStatus.RUNNING:
                return dataclasses.asdict(job)  # already the active job
            if job.status not in (JobStatus.PAUSED, JobStatus.WAITING_QUOTA, JobStatus.QUEUED):
                return dataclasses.asdict(job)
            current_active = self._active_job_id
            self._pending_next = job_id
            control = self._control if current_active else None
            worker = self._worker_thread if current_active else None
        if control:
            control.request_stop()
            if worker:
                worker.join(timeout=WORKER_STOP_JOIN_TIMEOUT)
        self._advance_queue()
        return self.get_job(job_id)

    def remove_job(self, job_id: str) -> bool:
        """Cancels and permanently forgets this job - if it's currently
        running, stops it cleanly first (like pause, but then discards it
        instead of leaving it resumable). Also deletes its on-disk progress
        file: without that, an "incomplete" job removed from the queue would
        silently reappear next time the app starts (load_incomplete_jobs
        re-queues anything incomplete it finds on disk).
        """
        with self._lock:
            if job_id not in self._jobs:
                return False
            is_active = job_id == self._active_job_id
        if is_active:
            self.pause(job_id)
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return False
            self._order.remove(job_id)
        if job.progress_path:
            Path(job.progress_path).unlink(missing_ok=True)
        return True

    def load_incomplete_jobs(self):
        """Called once at FastAPI startup - reloads any sync that didn't
        finish last time as a queued job, then lets the queue advance.
        """
        paths = sorted(PROJECT_DIR.glob("progress_*.json"), key=lambda p: p.stat().st_mtime)
        for path in paths:
            data = load_progress(path)
            if not data or len(data["results"]) >= len(data["tracks"]):
                continue
            job_id = uuid.uuid4().hex[:12]
            state = JobState(
                id=job_id,
                status=JobStatus.QUEUED,
                direction=data["direction"],
                name=data["name"],
                progress_path=str(path),
            )
            with self._lock:
                self._jobs[job_id] = state
                self._order.append(job_id)
                self._recompute_locked(state, data)
        self._advance_queue()

    def start_scheduler(self):
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()

    def shutdown(self):
        self._scheduler_stop.set()
        with self._lock:
            control = self._control
        if control:
            control.request_stop()

    # ---- internals ----

    def _scheduler_loop(self):
        while not self._scheduler_stop.is_set():
            self._advance_queue()
            self._scheduler_stop.wait(SCHEDULER_TICK_SECONDS)

    def _advance_queue(self):
        """If nothing is currently running, pick the best next job: an
        explicitly requested switch first (see resume()'s docstring), then an
        overdue quota/rate-limit wait (earliest due), else the oldest still-
        queued job. No-op if something is already active.
        """
        with self._lock:
            if self._active_job_id is not None:
                return
            candidate = None
            if self._pending_next and self._jobs.get(self._pending_next) and self._jobs[self._pending_next].status in (
                JobStatus.PAUSED, JobStatus.WAITING_QUOTA, JobStatus.QUEUED,
            ):
                candidate = self._pending_next
            self._pending_next = None
            if candidate is None:
                now = datetime.now(tz=YOUTUBE_TZ)
                due_waiting = [
                    jid for jid in self._order
                    if self._jobs[jid].status == JobStatus.WAITING_QUOTA
                    and self._jobs[jid].resume_at
                    and datetime.fromisoformat(self._jobs[jid].resume_at) <= now
                ]
                due_waiting.sort(key=lambda jid: self._jobs[jid].resume_at)
                if due_waiting:
                    candidate = due_waiting[0]
                else:
                    candidate = next((jid for jid in self._order if self._jobs[jid].status == JobStatus.QUEUED), None)
        if candidate:
            self._launch_worker(candidate)

    def _launch_worker(self, job_id: str):
        control = LoopControl()
        with self._lock:
            if self._active_job_id is not None:
                return  # lost a race to another advance - don't double-launch
            self._active_job_id = job_id
            self._control = control
            self._jobs[job_id].status = JobStatus.RUNNING
            self._jobs[job_id].resume_at = None
        thread = threading.Thread(target=self._run_worker, args=(job_id, control), daemon=True)
        with self._lock:
            self._worker_thread = thread
        thread.start()

    def _run_worker(self, job_id: str, control: LoopControl):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                direction, source, name, resume_path = job.direction, job.source, job.name, job.progress_path
        try:
            if job is None:
                return  # removed in the brief window between launch and here - the
                # finally block below still runs and frees up the active slot
            if direction == "spotify_to_youtube":
                self._run_spotify_to_youtube(job_id, source, name, resume_path, control)
            else:
                self._run_youtube_to_spotify(job_id, source, name, resume_path, control)
        except requests.exceptions.RequestException as e:
            # Network dropped before (or between) any of the per-track retry
            # handling could even run - e.g. during the initial track fetch,
            # or creating the destination playlist. Still just a blip, not a
            # reason to give up: retry automatically like any other rate limit.
            with self._lock:
                j = self._jobs.get(job_id)
                if j is not None:
                    j.status = JobStatus.WAITING_QUOTA
                    j.wait_reason = "rate_limit"
                    j.wait_detail = f"Network error: {e}"
                    j.resume_at = (datetime.now(tz=YOUTUBE_TZ) + timedelta(seconds=60)).isoformat()
        except Exception as e:
            # .get() rather than [] - the job may have been cancelled/removed
            # (see remove_job) while this worker was mid-failure; nothing left
            # to update in that case.
            with self._lock:
                j = self._jobs.get(job_id)
                if j is not None:
                    j.status = JobStatus.ERROR
                    j.error_message = str(e)
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    self._control = None
            self._advance_queue()

    def _run_spotify_to_youtube(self, job_id, source, name, resume_path, control):
        if resume_path:
            progress = load_progress(resume_path)
            progress_path = resume_path
        else:
            sp = get_spotify_client()
            liked = source == "liked"
            playlist_id = None if liked else s2y.extract_spotify_playlist_id(source)
            tracks = s2y.get_spotify_tracks(sp, playlist_id, liked)
            safe_name = re.sub(r"[^\w\-]+", "_", name)
            progress_path = str(PROJECT_DIR / f"progress_toyoutube_{safe_name}_{int(time.time())}.json")
            progress = {
                "direction": "spotify_to_youtube", "name": name, "tracks": tracks,
                "results": [], "youtube_playlist_id": None,
            }
            save_progress(progress_path, progress)

        with self._lock:
            job = self._jobs[job_id]
            job.progress_path = progress_path
            job.name = progress["name"]
            self._recompute_locked(job, progress)

        youtube = get_youtube_client()
        if not progress["youtube_playlist_id"]:
            progress["youtube_playlist_id"] = s2y.create_youtube_playlist(
                youtube, progress["name"], "Imported from Spotify"
            )
            save_progress(progress_path, progress)

        step = s2y.make_youtube_step(youtube, progress["youtube_playlist_id"])
        stop = run_sync_loop(
            progress, progress_path, progress["tracks"], step,
            control=control, on_progress=lambda p: self._on_progress(job_id, p), sleep_seconds=0.2,
        )
        self._finish(job_id, stop, f"https://www.youtube.com/playlist?list={progress['youtube_playlist_id']}")

    def _run_youtube_to_spotify(self, job_id, source, name, resume_path, control):
        if resume_path:
            progress = load_progress(resume_path)
            progress_path = resume_path
        else:
            youtube = get_youtube_client()
            playlist_id = y2s.extract_youtube_playlist_id(source)
            videos = y2s.get_youtube_playlist_tracks(youtube, playlist_id)
            safe_name = re.sub(r"[^\w\-]+", "_", name)
            progress_path = str(PROJECT_DIR / f"progress_tospotify_{safe_name}_{int(time.time())}.json")
            progress = {
                "direction": "youtube_to_spotify", "name": name, "tracks": videos,
                "results": [], "spotify_playlist_id": None,
            }
            save_progress(progress_path, progress)

        with self._lock:
            job = self._jobs[job_id]
            job.progress_path = progress_path
            job.name = progress["name"]
            self._recompute_locked(job, progress)

        sp = get_spotify_client()
        if not progress["spotify_playlist_id"]:
            progress["spotify_playlist_id"] = y2s.create_spotify_playlist(
                sp, progress["name"], "Imported from YouTube"
            )
            save_progress(progress_path, progress)

        step = y2s.make_spotify_step(sp, progress["spotify_playlist_id"])
        stop = run_sync_loop(
            progress, progress_path, progress["tracks"], step,
            control=control, on_progress=lambda p: self._on_progress(job_id, p), sleep_seconds=0.1,
        )
        self._finish(job_id, stop, f"https://open.spotify.com/playlist/{progress['spotify_playlist_id']}")

    def _on_progress(self, job_id, progress):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            self._recompute_locked(job, progress)
            r = progress["results"][-1]
            job.recent.insert(0, self._format_recent(progress["direction"], r))
            job.recent = job.recent[:25]

    @staticmethod
    def _recompute_locked(job: JobState, progress: dict):
        """Must be called with self._lock already held."""
        results = progress["results"]
        key = "video_id" if progress["direction"] == "spotify_to_youtube" else "track_uri"
        job.total = len(progress["tracks"])
        job.done = len(results)
        job.added = sum(1 for r in results if r.get("added"))
        job.not_found = sum(1 for r in results if not r.get("added") and not r.get(key))

    @staticmethod
    def _format_recent(direction, r):
        if direction == "spotify_to_youtube":
            status = "added" if r["added"] else ("no_match" if not r["video_id"] else "not_added")
            return {"title": r["name"], "subtitle": r["artists"], "status": status}
        status = "added" if r["added"] else ("no_match" if not r["track_uri"] else "not_added")
        return {"title": r["title"], "subtitle": None, "status": status}

    def _finish(self, job_id, stop, result_url):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if stop is None:
                job.status = JobStatus.DONE
                job.result_playlist_url = result_url
                job.resume_at = None
                job.error_message = None
            elif stop.kind == ErrorKind.QUOTA:
                # A real daily-cap hit: wait for the known reset window, not a
                # short backoff.
                job.status = JobStatus.WAITING_QUOTA
                job.wait_reason = "quota"
                resume_at = datetime.now(tz=YOUTUBE_TZ) + timedelta(seconds=stop.retry_after_seconds) \
                    if stop.retry_after_seconds else _next_youtube_quota_reset()
                job.resume_at = resume_at.isoformat()
                job.error_message = None
                job.wait_detail = stop.message
            elif stop.kind == ErrorKind.RATE_LIMIT:
                # A short-window rate limit or a transient server hiccup -
                # retry soon, never wait until tomorrow for this.
                job.status = JobStatus.WAITING_QUOTA
                job.wait_reason = "rate_limit"
                job.wait_detail = stop.message
                retry_seconds = stop.retry_after_seconds or 60
                job.resume_at = (datetime.now(tz=YOUTUBE_TZ) + timedelta(seconds=retry_seconds)).isoformat()
                job.error_message = None
            elif stop.kind == ErrorKind.FATAL:
                job.status = JobStatus.ERROR
                job.error_message = stop.message
            else:  # PAUSED (user pause, or a debug limit)
                job.status = JobStatus.PAUSED


job_manager = JobManager()
