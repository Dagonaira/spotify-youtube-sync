"""FastAPI app wiring the job manager to HTTP endpoints for the GUI frontend."""

import os
import threading
import webbrowser
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from spotipy.exceptions import SpotifyException

import common
from app.job_manager import job_manager

STATIC_DIR = common.app_root() / "app" / "static"
GITHUB_REPO_URL = "https://github.com/Dagonaira/spotify-youtube-sync"

app = FastAPI(title="Crossplay")

_connect_locks = {"spotify": threading.Lock(), "youtube": threading.Lock()}


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception):
    # The packaged .exe runs windowed - an unhandled exception would
    # otherwise vanish with no trace for either us or anyone we send this
    # to. Logging it here also means the API response carries a real reason
    # instead of a bare "Internal Server Error".
    common.log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


@app.on_event("startup")
def on_startup():
    job_manager.start_scheduler()
    job_manager.load_incomplete_jobs()


@app.get("/api/accounts")
def get_accounts():
    return {
        "spotify": common.spotify_account_status(),
        "youtube": common.youtube_account_status(),
    }


@app.post("/api/accounts/{service}/connect")
def connect_account(service: str):
    if service not in _connect_locks:
        raise HTTPException(status_code=404, detail="Unknown service")
    lock = _connect_locks[service]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=f"A {service} connection attempt is already in progress")
    try:
        if service == "spotify":
            try:
                sp = common.get_spotify_client()
                # spotipy builds the client lazily - it won't actually open
                # the browser and run the OAuth flow until the first real
                # API call.
                sp.current_user()
            except SpotifyException as e:
                common.log.warning("Spotify connect check failed: %s", e)
                if e.http_status == 429:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Spotify is rate-limiting this app right now (from heavy use) - "
                            "these blocks are usually several hours, sometimes closer to a "
                            "full day, before they clear on their own. No need to keep "
                            "retrying in the meantime; just try again later."
                        ),
                    )
                raise HTTPException(status_code=502, detail=f"Spotify error: {e}")
            return common.spotify_account_status(force=True)
        else:
            common.get_youtube_client()
            return common.youtube_account_status(force=True)
    finally:
        lock.release()


@app.get("/api/jobs")
def list_jobs():
    return job_manager.list_jobs()


class StartJobRequest(BaseModel):
    direction: str  # "spotify_to_youtube" | "youtube_to_spotify"
    source: str  # playlist URL/ID, or "liked" for Spotify Liked Songs
    name: str


@app.post("/api/jobs")
def add_job(req: StartJobRequest):
    if req.direction not in ("spotify_to_youtube", "youtube_to_spotify"):
        raise HTTPException(status_code=400, detail="Invalid direction")
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Playlist name is required")
    if not req.source.strip():
        raise HTTPException(status_code=400, detail="Source playlist is required")
    return job_manager.add_job(req.direction, req.source, req.name)


@app.get("/api/jobs/{job_id}/unmatched")
def get_unmatched_tracks(job_id: str):
    result = job_manager.get_unmatched(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return result


@app.get("/api/jobs/{job_id}/added")
def get_added_tracks(job_id: str):
    result = job_manager.get_added(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return result


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    result = job_manager.pause(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return result


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    result = job_manager.resume(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return result


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if not job_manager.remove_job(job_id):
        raise HTTPException(status_code=404, detail="Unknown job")
    return {"removed": job_id}


@app.post("/api/support/report-bug")
def report_bug():
    issue_body = (
        "**What happened:**\n\n\n"
        "**What you expected instead:**\n\n\n"
        "**Steps to reproduce:**\n\n\n"
        f"**Log file:** please attach `crossplay.log` if you can - find it via "
        "the \"Open log folder\" button next to this one, or at "
        f"`{common.LOG_FILE}`.\n"
    )
    url = f"{GITHUB_REPO_URL}/issues/new?" + urlencode({"title": "", "body": issue_body})
    # webbrowser.open opens the system's real default browser - not the app's
    # own embedded window, which isn't a full browsing environment.
    webbrowser.open(url)
    return {"opened": url}


@app.post("/api/support/open-log-folder")
def open_log_folder():
    os.startfile(common.DATA_DIR)  # noqa: S606 - Windows-only, opens File Explorer
    return {"opened": str(common.DATA_DIR)}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
