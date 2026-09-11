"""Shared auth clients and progress-file helpers for the sync scripts."""

import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# requests/httplib2 (used under the hood by spotipy and googleapiclient) have
# NO default timeout - a slow or unresponsive API otherwise hangs a request
# forever instead of failing. This is a global safety net so every outbound
# call in the app (account-status checks included) has a hard ceiling; actual
# failures are already handled gracefully (see sync_core's retry/backoff).
socket.setdefaulttimeout(15)

# spotipy's SpotifyOAuth specifically defaults requests_timeout=None, which
# under `requests` means "no timeout at all" (unlike spotipy.Spotify's own
# client calls, which already default to 5s) - the socket default above does
# NOT cover this, since `requests` explicitly overrides it. Pass this
# explicitly to every SpotifyOAuth(...) construction.
SPOTIFY_REQUEST_TIMEOUT = 15

def app_root() -> Path:
    """Where bundled, read-only app files live (the .env holding *our* shared
    Spotify credentials, and the Google client_secret.json) - the source tree
    when running from source, or PyInstaller's extracted bundle when frozen
    into an .exe. Same for every install; nobody edits these.
    """
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def get_data_dir() -> Path:
    """Per-user writable directory for this person's own login session
    (cache/token files) and in-progress sync jobs. Works the same running
    from source or as a packaged .exe: everyone who runs the app - us, or
    anyone we send it to - gets their own tokens here, isolated from
    whatever account ran it before them, without needing write access to
    wherever the app itself is installed.
    """
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "Crossplay"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve(data_path: Path, legacy_path: Path) -> Path:
    """Prefer an already-existing file at either location; new writes default
    to data_path. Lets this machine's existing dev setup (session files that
    predate the per-user data dir) keep working with zero migration step.
    """
    if data_path.exists():
        return data_path
    if legacy_path.exists():
        return legacy_path
    return data_path


SCRIPT_DIR = app_root()
load_dotenv(SCRIPT_DIR / ".env")

DATA_DIR = get_data_dir()

# The packaged .exe runs windowed (no console), so sys.stdout/stderr are None
# and any print()/traceback goes nowhere - without this, a crash in the GUI
# app would be completely invisible to both us and anyone we send it to.
LOG_FILE = DATA_DIR / "crossplay.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("crossplay")


def _log_thread_exception(args: threading.ExceptHookArgs):
    # Python's default behavior for an uncaught exception in a background
    # thread is to print it to stderr - which is None in the windowed .exe,
    # so it would otherwise vanish with no trace (the FastAPI-level handler
    # in app/server.py only covers exceptions raised from HTTP request
    # handlers, not background worker threads like JobManager's).
    log.error(
        "Unhandled exception in thread %r", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


threading.excepthook = _log_thread_exception

SPOTIFY_SCOPE = (
    "playlist-read-private playlist-read-collaborative user-library-read "
    "playlist-modify-private playlist-modify-public"
)
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]

# Shared app identity, bundled with the app - the same for every install.
YOUTUBE_CLIENT_SECRETS_FILE = SCRIPT_DIR / "client_secret.json"

# This person's own login session - never shared, never bundled.
YOUTUBE_TOKEN_FILE = _resolve(DATA_DIR / "youtube_token.json", SCRIPT_DIR / "youtube_token.json")
SPOTIFY_CACHE_FILE = _resolve(DATA_DIR / ".spotify_cache", SCRIPT_DIR / ".spotify_cache")


def get_spotify_client() -> spotipy.Spotify:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    if not client_id or not client_secret:
        sys.exit(
            "Missing SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.\n"
            "Copy .env.example to .env and fill in your Spotify app credentials."
        )
    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPE,
        requests_timeout=SPOTIFY_REQUEST_TIMEOUT,
        cache_path=str(SPOTIFY_CACHE_FILE),
        open_browser=True,
    )
    # retries=0: spotipy's default retry adapter honors the server's
    # Retry-After header and SLEEPS for it before giving up - on a real 429
    # that can be tens of thousands of seconds, hanging the whole call.
    # Disabling it lets our own sync_core retry/backoff handle 429s instead,
    # which fails fast and schedules a sane resume time.
    return spotipy.Spotify(auth_manager=auth_manager, retries=0)


def _load_youtube_credentials():
    """Load + refresh cached YouTube credentials only. Never opens a browser."""
    if not YOUTUBE_TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN_FILE), YOUTUBE_SCOPES)
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        YOUTUBE_TOKEN_FILE.write_text(creds.to_json())
    return creds


def get_youtube_client():
    creds = _load_youtube_credentials()
    if not creds or not creds.valid:
        if not YOUTUBE_CLIENT_SECRETS_FILE.exists():
            sys.exit(
                f"Missing {YOUTUBE_CLIENT_SECRETS_FILE.name}.\n"
                "Download your OAuth client secret JSON from Google Cloud Console "
                "and save it next to this script as client_secret.json."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(YOUTUBE_CLIENT_SECRETS_FILE), YOUTUBE_SCOPES
        )
        creds = flow.run_local_server(port=0)
        YOUTUBE_TOKEN_FILE.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


# The GUI polls /api/accounts every few seconds, and each check below makes
# a real network call to Spotify/YouTube just to confirm the cached token
# still works. With more than one client open at once (desktop app + a
# browser tab + the PWA, say) that adds up fast and can burn into the real
# provider's rate limit for no reason - the connection status barely ever
# changes second to second. A short TTL cache means concurrent/frequent
# pollers share one real check instead of each making their own.
_STATUS_CACHE_SECONDS = 20
_status_cache: dict[str, tuple[float, dict]] = {}


def _cached_status(key: str, compute, force: bool) -> dict:
    if not force:
        cached = _status_cache.get(key)
        if cached and time.time() - cached[0] < _STATUS_CACHE_SECONDS:
            return cached[1]
    result = compute()
    _status_cache[key] = (time.time(), result)
    return result


def spotify_account_status(force: bool = False) -> dict:
    return _cached_status("spotify", _spotify_account_status_live, force)


def _spotify_account_status_live() -> dict:
    """Cached-token state only. Never opens a browser or prompts for login."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret or not SPOTIFY_CACHE_FILE.exists():
        return {"connected": False, "account": None}
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPE,
        requests_timeout=SPOTIFY_REQUEST_TIMEOUT,
        cache_path=str(SPOTIFY_CACHE_FILE),
        open_browser=False,
    )
    token_info = auth_manager.cache_handler.get_cached_token()
    if not token_info:
        return {"connected": False, "account": None}
    if auth_manager.is_token_expired(token_info):
        try:
            token_info = auth_manager.refresh_access_token(token_info["refresh_token"])
        except Exception:
            return {"connected": False, "account": None}
    sp = spotipy.Spotify(auth=token_info["access_token"], retries=0)
    try:
        me = sp.current_user()
    except Exception:
        return {"connected": False, "account": None}
    return {"connected": True, "account": me.get("id")}


def youtube_account_status(force: bool = False) -> dict:
    return _cached_status("youtube", _youtube_account_status_live, force)


def _youtube_account_status_live() -> dict:
    """Cached-token state only. Never opens a browser or prompts for login."""
    creds = _load_youtube_credentials()
    if not creds or not creds.valid:
        return {"connected": False, "account": None}
    youtube = build("youtube", "v3", credentials=creds)
    try:
        response = youtube.channels().list(part="snippet", mine=True).execute()
    except Exception:
        return {"connected": False, "account": None}
    items = response.get("items", [])
    account = items[0]["snippet"]["title"] if items else None
    return {"connected": True, "account": account}


def load_progress(path):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_progress(path, progress: dict):
    """Atomic write: temp file in the same directory, then os.replace() over the target.

    Prevents a reader (the GUI's status endpoint) from ever seeing a truncated
    file while a worker thread is mid-write.
    """
    path = Path(path)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(progress, indent=2, ensure_ascii=False))
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
