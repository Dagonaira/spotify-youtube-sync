"""Shared retry/progress loop used by both the CLI (sync.py) and the GUI job manager.

Extracting this once means a fix to the pause/quota/resume behavior only has
to be made in one place instead of separately in spotify_to_youtube.py and
youtube_to_spotify.py.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from common import save_progress


class ErrorKind(Enum):
    QUOTA = "quota"              # safe to auto-retry after a known reset window
    RATE_LIMIT = "rate_limit"    # short-window, retry soon
    FATAL = "fatal"              # surface to the user, never auto-retry
    PAUSED = "paused"            # cooperative stop (user pause, or a CLI --limit) - resume anytime


@dataclass
class StopSignal:
    kind: ErrorKind
    retry_after_seconds: Optional[float] = None
    message: str = ""


@dataclass
class StepOutcome:
    # The dict to append to progress["results"], or None to record nothing
    # (used when a quota/rate-limit error hit before a definitive outcome was
    # reached, so the item is retried from scratch on resume instead of being
    # wrongly recorded as "no match found").
    result: Optional[dict]
    stop: Optional[StopSignal] = None


class LoopControl:
    """Cooperative pause flag shared between a worker thread and API handlers."""

    def __init__(self):
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def should_stop(self) -> bool:
        return self._stop_requested


def run_sync_loop(
    progress: dict,
    progress_path,
    items: list,
    step_fn: Callable[[dict], StepOutcome],
    *,
    control: Optional[LoopControl] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    sleep_seconds: float = 0.2,
    limit: Optional[int] = None,
) -> Optional[StopSignal]:
    """Run step_fn over items[len(progress['results']):], saving progress after
    every recorded item. Returns a StopSignal if stopped early (quota,
    rate-limit, fatal error, or a cooperative pause/limit), or None once every
    item has been processed.
    """
    control = control or LoopControl()
    start_index = len(progress["results"])
    processed_this_run = 0

    for i in range(start_index, len(items)):
        if control.should_stop():
            return StopSignal(kind=ErrorKind.PAUSED, message="paused")
        if limit is not None and processed_this_run >= limit:
            return StopSignal(kind=ErrorKind.PAUSED, message=f"limit {limit} reached")

        outcome = step_fn(items[i])

        if outcome.result is not None:
            progress["results"].append(outcome.result)
            save_progress(progress_path, progress)
            if on_progress:
                on_progress(progress)

        processed_this_run += 1

        if outcome.stop is not None:
            return outcome.stop

        time.sleep(sleep_seconds)

    return None
